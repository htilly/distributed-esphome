"""TG.3 wiring — :mod:`routing_eligibility.re_evaluate_routing` integrates the
queue + registry + scanner against the routing rule store.

These tests stand up a synthetic aiohttp Application with the same app
keys main.py wires up, then drive the sweep directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from job_queue import Job, JobQueue, JobState
from registry import WorkerRegistry
from routing import Clause, Rule, RoutingRuleStore
from routing_eligibility import re_evaluate_routing


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


def _write_yaml(d: Path, target: str, *, tags: list[str] | None = None,
                routing_extra: list[dict] | None = None) -> None:
    """Write a minimal YAML with an `# esphome-fleet:` metadata block."""
    lines = ["# esphome-fleet:"]
    if tags is not None:
        lines.append(f"#   tags: {','.join(tags)}")
    if routing_extra is not None:
        # Embed YAML for routing_extra inside the comment block.
        import yaml as _y
        rendered = _y.dump({"routing_extra": routing_extra}, default_flow_style=False).rstrip()
        for line in rendered.splitlines():
            lines.append(f"#   {line}")
    lines.append("")
    lines.append("esphome:")
    lines.append(f"  name: {target.replace('.yaml', '')}")
    (d / target).write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _make_app(tmp_path: Path, config_dir: Path) -> web.Application:
    """Stand up a minimal app with the keys re_evaluate_routing reads."""
    from app_config import AppConfig
    app = web.Application()
    app["config"] = AppConfig(config_dir=str(config_dir), port=0)
    app["queue"] = JobQueue(queue_file=tmp_path / "queue.json")
    app["registry"] = WorkerRegistry()
    app["routing_rule_store"] = RoutingRuleStore(path=tmp_path / "routing-rules.json")
    return app


async def _enqueue(app: web.Application, target: str = "kitchen.yaml") -> Job:
    job = await app["queue"].enqueue(
        target=target,
        esphome_version="2026.3.2",
        run_id="r1",
        timeout_seconds=300,
    )
    assert job is not None
    return job


def _add_worker(app: web.Application, *, hostname: str, tags: list[str]) -> str:
    """Register a worker into the in-memory registry, return client_id."""
    cid = app["registry"].register(
        hostname=hostname,
        platform="linux",
        client_version="dev",
        max_parallel_jobs=2,
        system_info=None,
        tags=tags,
    )
    return cid


# ---------------------------------------------------------------------------
# Empty/no-op cases
# ---------------------------------------------------------------------------


async def test_re_eval_no_rules_no_op(tmp_path: Path, config_dir: Path) -> None:
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "kitchen.yaml", tags=["kitchen"])
    _add_worker(app, hostname="w1", tags=["kitchen"])
    job = await _enqueue(app, "kitchen.yaml")
    assert job.state == JobState.PENDING

    changed = await re_evaluate_routing(app)
    assert changed == 0
    assert job.state == JobState.PENDING


async def test_re_eval_skips_terminal_jobs(tmp_path: Path, config_dir: Path) -> None:
    """SUCCESS / FAILED / CANCELLED jobs aren't touched even if a rule would block."""
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "k.yaml", tags=["kitchen"])
    job = await _enqueue(app, "k.yaml")
    job.state = JobState.SUCCESS

    # Add a rule that would otherwise block this device.
    app["routing_rule_store"].create_rule(Rule(
        id="kitchen-only",
        name="Kitchen only",
        severity="required",
        device_match=[Clause(op="all_of", tags=["kitchen"])],
        worker_match=[Clause(op="all_of", tags=["nonexistent"])],
    ))

    changed = await re_evaluate_routing(app)
    assert changed == 0
    assert job.state == JobState.SUCCESS


# ---------------------------------------------------------------------------
# PENDING → BLOCKED transitions
# ---------------------------------------------------------------------------


async def test_re_eval_pending_to_blocked_when_no_eligible_worker(
    tmp_path: Path, config_dir: Path,
) -> None:
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "kitchen.yaml", tags=["kitchen"])
    # Worker exists but lacks the required "kitchen" tag.
    _add_worker(app, hostname="w1", tags=["office"])
    job = await _enqueue(app, "kitchen.yaml")

    app["routing_rule_store"].create_rule(Rule(
        id="kitchen-only",
        name="Kitchen build only",
        severity="required",
        device_match=[Clause(op="all_of", tags=["kitchen"])],
        worker_match=[Clause(op="all_of", tags=["kitchen"])],
    ))

    changed = await re_evaluate_routing(app)
    assert changed == 1
    assert job.state == JobState.BLOCKED
    assert job.blocked_reason is not None
    assert job.blocked_reason["rule_id"] == "kitchen-only"
    assert job.blocked_reason["rule_name"] == "Kitchen build only"
    assert "kitchen" in job.blocked_reason["summary"]


async def test_re_eval_blocked_to_pending_when_eligible_worker_arrives(
    tmp_path: Path, config_dir: Path,
) -> None:
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "kitchen.yaml", tags=["kitchen"])
    _add_worker(app, hostname="office-only", tags=["office"])
    job = await _enqueue(app, "kitchen.yaml")
    app["routing_rule_store"].create_rule(Rule(
        id="kitchen-only",
        name="Kitchen build only",
        severity="required",
        device_match=[Clause(op="all_of", tags=["kitchen"])],
        worker_match=[Clause(op="all_of", tags=["kitchen"])],
    ))
    # First sweep blocks.
    await re_evaluate_routing(app)
    assert job.state == JobState.BLOCKED

    # A new eligible worker registers.
    _add_worker(app, hostname="kitchen-w", tags=["kitchen"])

    changed = await re_evaluate_routing(app)
    assert changed == 1
    assert job.state == JobState.PENDING
    assert job.blocked_reason is None


async def test_re_eval_idempotent_on_steady_state(
    tmp_path: Path, config_dir: Path,
) -> None:
    """Sequential sweeps don't churn — second call sees zero changes."""
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "kitchen.yaml", tags=["kitchen"])
    _add_worker(app, hostname="w", tags=["office"])
    await _enqueue(app, "kitchen.yaml")
    app["routing_rule_store"].create_rule(Rule(
        id="kitchen-only",
        name="Kitchen build only",
        severity="required",
        device_match=[Clause(op="all_of", tags=["kitchen"])],
        worker_match=[Clause(op="all_of", tags=["kitchen"])],
    ))

    first = await re_evaluate_routing(app)
    second = await re_evaluate_routing(app)
    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# Per-device routing_extra
# ---------------------------------------------------------------------------


async def test_re_eval_honours_per_device_routing_extra(
    tmp_path: Path, config_dir: Path,
) -> None:
    """A device's own additive rule blocks even when the global list is empty."""
    app = await _make_app(tmp_path, config_dir)
    # Device demands a worker tagged "fast" via per-device routing_extra.
    _write_yaml(
        config_dir,
        "kitchen.yaml",
        tags=["kitchen"],
        routing_extra=[{
            "name": "needs fast",
            "device_match": [{"op": "all_of", "tags": ["kitchen"]}],
            "worker_match": [{"op": "all_of", "tags": ["fast"]}],
        }],
    )
    # Worker has kitchen but not fast.
    _add_worker(app, hostname="slow-kitchen", tags=["kitchen"])
    job = await _enqueue(app, "kitchen.yaml")

    changed = await re_evaluate_routing(app)
    assert changed == 1
    assert job.state == JobState.BLOCKED
    assert job.blocked_reason is not None
    assert job.blocked_reason["rule_name"] == "needs fast"


# ---------------------------------------------------------------------------
# Offline-worker handling
# ---------------------------------------------------------------------------


async def test_re_eval_ignores_offline_workers(
    tmp_path: Path, config_dir: Path,
) -> None:
    """An offline worker can't unblock a job, even if its tags would match."""
    app = await _make_app(tmp_path, config_dir)
    _write_yaml(config_dir, "kitchen.yaml", tags=["kitchen"])
    cid = _add_worker(app, hostname="kitchen-w", tags=["kitchen"])
    job = await _enqueue(app, "kitchen.yaml")
    app["routing_rule_store"].create_rule(Rule(
        id="kitchen-only",
        name="Kitchen build only",
        severity="required",
        device_match=[Clause(op="all_of", tags=["kitchen"])],
        worker_match=[Clause(op="all_of", tags=["kitchen"])],
    ))

    # Force the worker offline by backdating its last_seen past the
    # default 30 s threshold.
    w = app["registry"].get(cid)
    assert w is not None
    from datetime import timedelta
    w.last_seen = w.last_seen - timedelta(seconds=120)

    changed = await re_evaluate_routing(app)
    assert changed == 1
    assert job.state == JobState.BLOCKED
