"""Bug #111 regression: ``create_bundle_async`` serialises concurrent invocations.

ESPHome's ``git.clone_or_update`` (``esphome/git.py``) has no inter-process
file lock. When two bundle subprocesses race on the same destination
directory ``<config_dir>/.esphome/<domain>/<sha8>/`` (because two queued
jobs reference the same ``packages:`` / ``external_components:`` git repo
with cold caches), the loser observes a partial-state tree and surfaces a
different validation error per step — "Could not find components folder
for source", "<file> does not exist in repository", or ``AssertionError``
from a half-merged packages-pass.

The fix in :func:`scanner.create_bundle_async` wraps the subprocess
dispatch in an ``asyncio.Lock`` so only one bundle runs at a time. This
test pins that contract: if someone strips the lock or refactors the
wrapper to dispatch concurrently, ``max_in_flight`` rises above 1 and the
test fails.

Live-network reproduction details and cold/warm A/B verification live in
``dev-plans/WORKITEMS-1.7.0.md`` under bug #111.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import scanner


async def test_create_bundle_async_serialises_concurrent_calls():
    in_flight = 0
    max_in_flight = 0
    counter_lock = threading.Lock()

    def fake_create_bundle(config_dir: str, target: str) -> bytes:
        nonlocal in_flight, max_in_flight
        with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        # Long enough that overlap would be observable if the lock
        # were absent: asyncio.gather + run_in_executor with the
        # default thread pool runs N executors concurrently. 50 ms is
        # well above the gather + dispatch overhead.
        time.sleep(0.05)
        with counter_lock:
            in_flight -= 1
        return f"bundle:{target}".encode()

    with patch.object(scanner, "create_bundle", side_effect=fake_create_bundle):
        results = await asyncio.gather(
            scanner.create_bundle_async("/cfg", "a.yaml"),
            scanner.create_bundle_async("/cfg", "b.yaml"),
            scanner.create_bundle_async("/cfg", "c.yaml"),
        )

    assert results == [b"bundle:a.yaml", b"bundle:b.yaml", b"bundle:c.yaml"]
    assert max_in_flight == 1, (
        f"create_bundle_async failed to serialise concurrent calls — "
        f"saw {max_in_flight} overlapping invocations (expected 1)"
    )


async def test_create_bundle_async_propagates_subprocess_errors():
    """The lock must not swallow exceptions raised by ``create_bundle``."""

    def boom(config_dir: str, target: str) -> bytes:
        raise RuntimeError("validation errors (1 total): something is wrong")

    with patch.object(scanner, "create_bundle", side_effect=boom):
        try:
            await scanner.create_bundle_async("/cfg", "broken.yaml")
        except RuntimeError as exc:
            assert "validation errors" in str(exc)
        else:
            raise AssertionError(
                "Expected RuntimeError to propagate through create_bundle_async"
            )


async def test_create_bundle_async_releases_lock_after_failure():
    """A failed bundle must release the lock so the next call proceeds."""

    calls = []

    def fake_create_bundle(config_dir: str, target: str) -> bytes:
        calls.append(target)
        if target == "broken.yaml":
            raise RuntimeError("validation errors (1 total): bad yaml")
        return f"bundle:{target}".encode()

    with patch.object(scanner, "create_bundle", side_effect=fake_create_bundle):
        try:
            await scanner.create_bundle_async("/cfg", "broken.yaml")
        except RuntimeError:
            pass
        # Without lock release on exception, this would hang forever.
        result = await asyncio.wait_for(
            scanner.create_bundle_async("/cfg", "ok.yaml"),
            timeout=2.0,
        )

    assert result == b"bundle:ok.yaml"
    assert calls == ["broken.yaml", "ok.yaml"]
