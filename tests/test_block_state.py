"""TG.3 — BLOCKED job state transitions.

Covers the data-model side of TG.3: ``JobState.BLOCKED``, the
``Job.blocked_reason`` field, and the new
``JobQueue.re_evaluate_routing(check_eligibility)`` sweep that flips
PENDING ↔ BLOCKED based on a caller-supplied eligibility predicate.
The wiring of the predicate (rule store + registry + scanner) is
caller-side and lives in api.py / ui_api.py / main.py — those are
exercised by the matrix smoke. This file pins the queue logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from job_queue import Job, JobQueue, JobState


@pytest.fixture
async def queue(tmp_path: Path) -> JobQueue:
    q = JobQueue(queue_file=tmp_path / "queue.json")
    return q


async def _enqueue_pending(q: JobQueue, target: str = "kitchen.yaml") -> Job:
    job = await q.enqueue(
        target=target,
        esphome_version="2026.3.2",
        run_id="r1",
        timeout_seconds=300,
    )
    assert job is not None
    return job


# ---------------------------------------------------------------------------
# Data-model sanity
# ---------------------------------------------------------------------------


async def test_blocked_state_serialises_round_trip(tmp_path: Path) -> None:
    q1 = JobQueue(queue_file=tmp_path / "queue.json")
    job = await _enqueue_pending(q1)
    job.state = JobState.BLOCKED
    job.blocked_reason = {"rule_id": "kitchen-only", "rule_name": "Kitchen rule", "summary": "needs kitchen tag"}
    q1._persist()  # noqa: SLF001 — direct test scaffolding

    q2 = JobQueue(queue_file=tmp_path / "queue.json")
    q2.load()
    loaded = q2.get(job.id)
    assert loaded is not None
    assert loaded.state == JobState.BLOCKED
    assert loaded.blocked_reason == job.blocked_reason


async def test_blocked_state_in_to_dict(queue: JobQueue) -> None:
    job = await _enqueue_pending(queue)
    job.state = JobState.BLOCKED
    job.blocked_reason = {"rule_id": "r1", "rule_name": "r1", "summary": "x"}
    d = job.to_dict()
    assert d["state"] == "blocked"
    assert d["blocked_reason"] == {"rule_id": "r1", "rule_name": "r1", "summary": "x"}


# ---------------------------------------------------------------------------
# claim_next skips BLOCKED jobs
# ---------------------------------------------------------------------------


async def test_claim_next_skips_blocked_jobs(queue: JobQueue) -> None:
    blocked = await _enqueue_pending(queue, target="blocked.yaml")
    pending = await _enqueue_pending(queue, target="pending.yaml")
    blocked.state = JobState.BLOCKED
    blocked.blocked_reason = {"rule_id": "r1", "rule_name": "r1", "summary": "no eligible worker"}
    # Polling worker should claim the PENDING one, not the BLOCKED one.
    claimed = await queue.claim_next("worker-1")
    assert claimed is not None
    assert claimed.id == pending.id


async def test_claim_next_returns_none_when_only_blocked(queue: JobQueue) -> None:
    blocked = await _enqueue_pending(queue)
    blocked.state = JobState.BLOCKED
    blocked.blocked_reason = {"rule_id": "r1", "rule_name": "r1", "summary": "x"}
    assert await queue.claim_next("worker-1") is None


# ---------------------------------------------------------------------------
# re_evaluate_routing — PENDING ↔ BLOCKED transitions
# ---------------------------------------------------------------------------


async def test_re_evaluate_pending_to_blocked(queue: JobQueue) -> None:
    job = await _enqueue_pending(queue)
    reason = {"rule_id": "r1", "rule_name": "r1", "summary": "no eligible worker"}

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        return (False, reason)

    changed = await queue.re_evaluate_routing(check)
    assert changed == 1
    assert queue.get(job.id).state == JobState.BLOCKED
    assert queue.get(job.id).blocked_reason == reason


async def test_re_evaluate_blocked_to_pending(queue: JobQueue) -> None:
    job = await _enqueue_pending(queue)
    job.state = JobState.BLOCKED
    job.blocked_reason = {"rule_id": "r1", "rule_name": "r1", "summary": "x"}

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        return (True, None)

    changed = await queue.re_evaluate_routing(check)
    assert changed == 1
    loaded = queue.get(job.id)
    assert loaded.state == JobState.PENDING
    assert loaded.blocked_reason is None


async def test_re_evaluate_skips_terminal_states(queue: JobQueue) -> None:
    """SUCCESS / FAILED / CANCELLED jobs are out of scope — re-eval ignores them."""
    job = await _enqueue_pending(queue)
    job.state = JobState.SUCCESS

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        # If we got called, fail loudly — terminal states shouldn't reach
        # the eligibility check.
        raise AssertionError(f"check_eligibility called for terminal job {j.id}")

    changed = await queue.re_evaluate_routing(check)
    assert changed == 0
    assert queue.get(job.id).state == JobState.SUCCESS


async def test_re_evaluate_idempotent_on_steady_state(queue: JobQueue) -> None:
    """Watchdog-friendly: calling re_evaluate twice with the same eligibility
    function and unchanged data must not bounce the state."""
    job = await _enqueue_pending(queue)
    reason = {"rule_id": "r1", "rule_name": "r1", "summary": "x"}

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        return (False, reason)

    assert await queue.re_evaluate_routing(check) == 1
    assert await queue.re_evaluate_routing(check) == 0  # no-op second pass


async def test_re_evaluate_updates_stale_reason_silently(queue: JobQueue) -> None:
    """Rule renamed → blocked_reason summary changes but state stays BLOCKED."""
    job = await _enqueue_pending(queue)
    job.state = JobState.BLOCKED
    job.blocked_reason = {"rule_id": "r1", "rule_name": "Old name", "summary": "old"}

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        return (False, {"rule_id": "r1", "rule_name": "New name", "summary": "new"})

    changed = await queue.re_evaluate_routing(check)
    # Counts as 0 changed (state didn't change), but reason was refreshed.
    assert changed == 0
    assert queue.get(job.id).state == JobState.BLOCKED
    assert queue.get(job.id).blocked_reason == {"rule_id": "r1", "rule_name": "New name", "summary": "new"}


async def test_re_evaluate_clears_stale_reason_on_pending(queue: JobQueue) -> None:
    """A PENDING job carrying a leftover blocked_reason gets it cleared
    (e.g. a worker became eligible between two re-eval calls)."""
    job = await _enqueue_pending(queue)
    # Hand-set: job is PENDING but has a stale blocked_reason
    # (this shouldn't happen in steady state but defensive code paths exist).
    job.blocked_reason = {"rule_id": "r1", "rule_name": "r1", "summary": "stale"}

    def check(j: Job) -> tuple[bool, Optional[dict]]:
        return (True, None)

    await queue.re_evaluate_routing(check)
    assert queue.get(job.id).blocked_reason is None


# ---------------------------------------------------------------------------
# Bug #95 — claim_next per-worker eligibility filter
# ---------------------------------------------------------------------------


async def test_claim_next_skips_jobs_when_predicate_rejects(queue: JobQueue) -> None:
    """A PENDING job with at least one fleet-eligible worker stays PENDING
    (re_evaluate's correct call) — but the *ineligible* worker calling
    claim_next must not snatch it. Without the per-worker check, every
    online worker could grab the job, defeating required rules."""
    job = await _enqueue_pending(queue, target="garage-door-big.yaml")

    def is_eligible(j: Job) -> bool:
        # Stand-in for "this worker's tags don't satisfy the rule for j".
        return False

    claimed = await queue.claim_next(
        client_id="debian-worker", worker_id=1, hostname="local-worker",
        is_eligible=is_eligible,
    )
    assert claimed is None
    # Job stayed PENDING — the eligible worker can still grab it later.
    assert queue.get(job.id).state == JobState.PENDING


async def test_claim_next_accepts_jobs_when_predicate_passes(queue: JobQueue) -> None:
    """Symmetry — a passing predicate doesn't block the claim path."""
    job = await _enqueue_pending(queue, target="garage-door-big.yaml")

    claimed = await queue.claim_next(
        client_id="windows-worker", worker_id=1, hostname="OPTIPLEX-7",
        is_eligible=lambda _j: True,
    )
    assert claimed is not None
    assert claimed.id == job.id
    assert queue.get(job.id).state == JobState.WORKING


async def test_claim_next_predicate_bypassed_for_pinned(queue: JobQueue) -> None:
    """Pinned jobs are the user's explicit override — even an ineligible
    pinned target should be claimable by the pinned worker. (Mismatched
    pin + rule is a user-visible conflict, not something we silently
    strand the job for.)"""
    job = await queue.enqueue(
        target="garage-door-big.yaml",
        esphome_version="2026.3.2",
        run_id="r1",
        timeout_seconds=300,
        pinned_client_id="debian-worker",
    )
    assert job is not None

    claimed = await queue.claim_next(
        client_id="debian-worker", worker_id=1, hostname="local-worker",
        # Predicate would normally reject this debian worker for ratgdo
        # devices, but the pin bypasses the eligibility filter.
        is_eligible=lambda _j: False,
    )
    assert claimed is not None
    assert claimed.id == job.id


async def test_claim_next_predicate_omitted_is_backwards_compatible(queue: JobQueue) -> None:
    """Callers that don't supply ``is_eligible`` keep the old behaviour
    (no per-worker filtering)."""
    job = await _enqueue_pending(queue, target="anything.yaml")
    claimed = await queue.claim_next(
        client_id="any-worker", worker_id=1, hostname="any-host",
    )
    assert claimed is not None
    assert claimed.id == job.id
