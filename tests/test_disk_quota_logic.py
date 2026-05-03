"""Tests for DQ.7 — worker disk-quota engine.

PY-10 ``_logic`` suffix: this is a pure-logic suite over synthetic
filesystem trees with controlled mtimes. No HA imports, no real ESPHome
install — every test is deterministic.

Coverage map:

- :func:`compute_usage` accounting per category
- :func:`prune_orphans` for ``N >= max_slots``
- :func:`enforce_quota` eviction ORDER across all four categories
- Pinning honored at every step
- Per-target unit eviction (cache + every slots/<N>/<stem>/ go together)
- Idempotency on a steady-state second pass
- Sweep on quota lowered mid-life
- ``ActiveJobSet`` refcounting + concurrent pins
- Pinned-only-exceeds-quota → warn + no-op (review concern surfaced
  during the executing-plans critical review)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import pytest

from disk_quota import (
    ActiveJobSet,
    PinnedSet,
    SweepResult,
    compute_usage,
    enforce_quota,
    host_disk_floor,
    prune_orphans,
)


# ---------------------------------------------------------------------------
# Synthetic-tree builders
# ---------------------------------------------------------------------------


def _write_blob(path: Path, size: int) -> None:
    """Create a file of exactly ``size`` bytes at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * size)


def _make_venv(base: Path, version: str, *, total_size: int, mtime: float) -> Path:
    """Synthesize an ESPHome venv at ``base/<version>/`` with a fake esphome bin.

    ``total_size`` is the exact byte count of the venv (the marker file is
    zero-sized so callers can pin precise expectations).
    """
    vdir = base / version
    bin_dir = vdir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    # The "bin/esphome" marker file is zero-sized so total_size is exact.
    (bin_dir / "esphome").write_bytes(b"")
    _write_blob(vdir / "lib" / "padding", total_size)
    os.utime(vdir, (mtime, mtime))
    return vdir


def _make_target_cache(
    base: Path, stem: str, *, cache_size: int, mtime: float
) -> Path:
    """``cache/<stem>/`` populated with a single blob of ``cache_size``."""
    cache_dir = base / "cache" / stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_blob(cache_dir / "blob", cache_size)
    os.utime(cache_dir, (mtime, mtime))
    return cache_dir


def _make_slot_target(
    base: Path, slot_id: int, stem: str, *, size: int, mtime: float = None
) -> Path:
    """``slots/<N>/<stem>/`` populated with a single blob."""
    slot_dir = base / "slots" / str(slot_id) / stem
    slot_dir.mkdir(parents=True, exist_ok=True)
    _write_blob(slot_dir / "blob", size)
    if mtime is not None:
        os.utime(slot_dir, (mtime, mtime))
    return slot_dir


def _make_pio_slot(base: Path, slot_id: int, *, size: int, mtime: float) -> Path:
    """``pio-slot-<N>/`` populated with a single blob."""
    pio = base / f"pio-slot-{slot_id}"
    pio.mkdir(parents=True, exist_ok=True)
    _write_blob(pio / "blob", size)
    os.utime(pio, (mtime, mtime))
    return pio


# ---------------------------------------------------------------------------
# compute_usage
# ---------------------------------------------------------------------------


def test_compute_usage_attributes_each_category(tmp_path: Path) -> None:
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=1.0)
    _make_target_cache(base, "device-a", cache_size=200, mtime=1.0)
    _make_slot_target(base, 1, "device-a", size=300)
    _make_pio_slot(base, 1, size=400, mtime=1.0)

    u = compute_usage(base)
    assert u.venv_bytes == 100
    assert u.cache_bytes == 200
    assert u.slot_bytes == 300
    assert u.pio_slot_bytes == 400
    assert u.total_bytes == 1000


def test_compute_usage_empty_base(tmp_path: Path) -> None:
    u = compute_usage(tmp_path)
    assert u.total_bytes == 0


def test_compute_usage_missing_base(tmp_path: Path) -> None:
    u = compute_usage(tmp_path / "does-not-exist")
    assert u.total_bytes == 0


def test_compute_usage_ignores_other_top_level_files(tmp_path: Path) -> None:
    """Top-level files (e.g. .client_id) get bucketed as 'other'."""
    (tmp_path / ".client_id").write_text("abc")
    u = compute_usage(tmp_path)
    assert u.venv_bytes == 0
    assert u.other_bytes == 3


# ---------------------------------------------------------------------------
# prune_orphans
# ---------------------------------------------------------------------------


def test_prune_orphans_removes_slots_above_max(tmp_path: Path) -> None:
    """Slot dirs with id > max_slots evicted unconditionally.

    Worker thread ids are 1..max_slots (see ``client.start_workers`` →
    ``args=(i + 1, ...)``). With max_slots=2 the valid ids are {1, 2};
    ids 3+ are orphans from a downsize. Regression for the off-by-one
    that previously deleted the highest live slot.
    """
    base = tmp_path
    _make_slot_target(base, 1, "stem-a", size=10)
    _make_slot_target(base, 2, "stem-a", size=10)  # live (was wrongly evicted pre-fix)
    _make_slot_target(base, 3, "stem-a", size=20)  # orphan
    _make_slot_target(base, 5, "stem-a", size=30)  # orphan
    _make_pio_slot(base, 2, size=10, mtime=1.0)    # live
    _make_pio_slot(base, 5, size=40, mtime=1.0)    # orphan

    result = prune_orphans(base, max_slots=2)
    assert result.orphan_slots_evicted == 2
    assert result.freed_bytes == 20 + 30 + 40
    assert (base / "slots" / "1").exists()
    assert (base / "slots" / "2").exists()
    assert not (base / "slots" / "3").exists()
    assert not (base / "slots" / "5").exists()
    assert not (base / "pio-slot-5").exists()
    assert (base / "pio-slot-2").exists()


def test_prune_orphans_keeps_in_range_slots(tmp_path: Path) -> None:
    base = tmp_path
    _make_slot_target(base, 1, "stem-a", size=10)
    _make_slot_target(base, 2, "stem-a", size=20)
    _make_slot_target(base, 3, "stem-a", size=30)

    result = prune_orphans(base, max_slots=3)
    assert result.orphan_slots_evicted == 0
    assert (base / "slots" / "1").exists()
    assert (base / "slots" / "2").exists()
    assert (base / "slots" / "3").exists()


def test_prune_orphans_handles_pio_slot_only(tmp_path: Path) -> None:
    """A pio-slot dir without a matching slots/<N>/ is still pruned."""
    base = tmp_path
    _make_pio_slot(base, 9, size=50, mtime=1.0)
    result = prune_orphans(base, max_slots=2)
    assert result.orphan_slots_evicted == 1
    assert result.freed_bytes == 50


def test_prune_orphans_keeps_highest_live_slot(tmp_path: Path) -> None:
    """Regression: the highest live slot (id == max_slots) must survive.

    Pre-fix the guard was ``slot_id < max_slots`` — with the default
    MAX_PARALLEL_JOBS=2, slot 2 (the second live worker) got nuked under
    a running compile on every prune sweep.
    """
    base = tmp_path
    _make_slot_target(base, 2, "live-stem", size=100)
    _make_pio_slot(base, 2, size=100, mtime=1.0)

    result = prune_orphans(base, max_slots=2)

    assert result.orphan_slots_evicted == 0
    assert (base / "slots" / "2").exists(), "live worker's slot was evicted"
    assert (base / "pio-slot-2").exists(), "live worker's pio-slot was evicted"


# ---------------------------------------------------------------------------
# enforce_quota — eviction order
# ---------------------------------------------------------------------------


def test_enforce_quota_evicts_stale_venvs_first(tmp_path: Path) -> None:
    """Step 1: collapse to 1 venv (MRU kept), no caches touched."""
    base = tmp_path
    _make_venv(base, "2026.4.1", total_size=1000, mtime=1.0)  # oldest
    _make_venv(base, "2026.4.2", total_size=1000, mtime=2.0)
    _make_venv(base, "2026.4.3", total_size=1000, mtime=3.0)  # MRU
    _make_target_cache(base, "device-a", cache_size=500, mtime=10.0)

    # Quota: keep MRU venv (1000) + cache (500) = 1500. Evict 2 oldest venvs.
    result = enforce_quota(base, quota_bytes=1500, pinned=PinnedSet())
    assert result.venvs_evicted == 2
    assert result.targets_evicted == 0
    assert (base / "2026.4.3").exists()  # MRU kept
    assert not (base / "2026.4.1").exists()
    assert not (base / "2026.4.2").exists()
    assert (base / "cache" / "device-a").exists()


def test_enforce_quota_evicts_oldest_target_after_venvs(tmp_path: Path) -> None:
    """Step 2: per-target caches in mtime-LRU order, only after venvs."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=500, mtime=10.0)
    _make_target_cache(base, "older", cache_size=300, mtime=1.0)
    _make_target_cache(base, "newer", cache_size=300, mtime=2.0)

    # Quota = venv (500) + 1 cache (300) = 800. Evict the older cache.
    result = enforce_quota(base, quota_bytes=800, pinned=PinnedSet())
    assert result.venvs_evicted == 0
    assert result.targets_evicted == 1
    assert not (base / "cache" / "older").exists()
    assert (base / "cache" / "newer").exists()


def test_enforce_quota_evicts_target_as_a_unit(tmp_path: Path) -> None:
    """cache/<stem>/ + every slots/<N>/<stem>/ go together."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_target_cache(base, "stem-a", cache_size=300, mtime=1.0)
    _make_slot_target(base, 1, "stem-a", size=200)
    _make_slot_target(base, 2, "stem-a", size=200)

    # Quota = venv only. Target eviction must remove all 3 dirs (1 unit).
    result = enforce_quota(base, quota_bytes=100, pinned=PinnedSet())
    assert result.targets_evicted == 1
    assert not (base / "cache" / "stem-a").exists()
    assert not (base / "slots" / "1" / "stem-a").exists()
    assert not (base / "slots" / "2" / "stem-a").exists()


def test_enforce_quota_evicts_pio_slots_last(tmp_path: Path) -> None:
    """Step 3: pio-slot toolchains only after caches are exhausted."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    # No caches.
    _make_pio_slot(base, 1, size=500, mtime=1.0)  # older
    _make_pio_slot(base, 2, size=500, mtime=2.0)

    # Quota = venv + 1 pio-slot. Evict the older one.
    result = enforce_quota(base, quota_bytes=600, pinned=PinnedSet())
    assert result.pio_slots_evicted == 1
    assert not (base / "pio-slot-1").exists()
    assert (base / "pio-slot-2").exists()


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


def test_enforce_quota_skips_pinned_venv(tmp_path: Path) -> None:
    """Pinned venv survives even if it's not MRU and over quota."""
    base = tmp_path
    _make_venv(base, "2026.4.1", total_size=1000, mtime=1.0)  # pinned but old
    _make_venv(base, "2026.4.3", total_size=1000, mtime=3.0)  # MRU

    pinned = PinnedSet(venv_versions={"2026.4.1"})
    enforce_quota(base, quota_bytes=2500, pinned=pinned)
    assert (base / "2026.4.1").exists()  # pinned, kept despite older
    assert (base / "2026.4.3").exists()


def test_enforce_quota_skips_pinned_target(tmp_path: Path) -> None:
    """Pinned target survives even if it's the oldest."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_target_cache(base, "pinned", cache_size=500, mtime=1.0)  # oldest
    _make_target_cache(base, "younger", cache_size=500, mtime=2.0)

    pinned = PinnedSet(target_stems={"pinned"})
    # Quota too tight for both caches; engine must evict 'younger' since
    # 'pinned' is off-limits even though it's older.
    enforce_quota(base, quota_bytes=600, pinned=pinned)
    assert (base / "cache" / "pinned").exists()
    assert not (base / "cache" / "younger").exists()


def test_enforce_quota_skips_pinned_pio_slot(tmp_path: Path) -> None:
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_pio_slot(base, 1, size=500, mtime=1.0)  # pinned, oldest
    _make_pio_slot(base, 2, size=500, mtime=2.0)

    pinned = PinnedSet(slot_ids={1})
    enforce_quota(base, quota_bytes=600, pinned=pinned)
    assert (base / "pio-slot-1").exists()
    assert not (base / "pio-slot-2").exists()


def test_enforce_quota_pinned_only_exceeds_quota_warns_no_op(
    tmp_path: Path, caplog
) -> None:
    """Review concern: when only pinned items remain and usage > quota the
    engine logs a warning and returns rather than panicking. The next
    post-job sweep will catch up once pins drop."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=10000, mtime=10.0)
    _make_target_cache(base, "stem-a", cache_size=10000, mtime=1.0)

    pinned = PinnedSet(
        venv_versions={"2026.4.3"},
        target_stems={"stem-a"},
    )
    with caplog.at_level(logging.WARNING, logger="disk_quota"):
        result = enforce_quota(base, quota_bytes=100, pinned=pinned)

    assert result.freed_bytes == 0
    assert (base / "2026.4.3").exists()
    assert (base / "cache" / "stem-a").exists()
    assert any("only pinned items remain" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Idempotency + quota-lowered-mid-life
# ---------------------------------------------------------------------------


def test_enforce_quota_idempotent_on_steady_state(tmp_path: Path) -> None:
    """Second pass on an already-converged tree is a no-op."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=500, mtime=10.0)
    _make_target_cache(base, "stem-a", cache_size=300, mtime=2.0)

    first = enforce_quota(base, quota_bytes=10000, pinned=PinnedSet())
    second = enforce_quota(base, quota_bytes=10000, pinned=PinnedSet())
    # Plenty of headroom — neither pass evicts anything.
    assert first.freed_bytes == 0
    assert second.freed_bytes == 0


def test_enforce_quota_sweep_on_lower_quota(tmp_path: Path) -> None:
    """Quota lowered mid-life triggers eviction on the next sweep."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_target_cache(base, "older", cache_size=300, mtime=1.0)
    _make_target_cache(base, "newer", cache_size=300, mtime=2.0)
    _make_pio_slot(base, 1, size=500, mtime=1.0)

    # First pass: spacious quota, nothing evicted.
    r1 = enforce_quota(base, quota_bytes=10000, pinned=PinnedSet())
    assert r1.freed_bytes == 0

    # Quota lowered by half; oldest cache must go.
    r2 = enforce_quota(base, quota_bytes=900, pinned=PinnedSet())
    assert r2.targets_evicted == 1
    assert not (base / "cache" / "older").exists()
    assert (base / "cache" / "newer").exists()

    # Quota lowered further; pio-slot eviction kicks in.
    r3 = enforce_quota(base, quota_bytes=400, pinned=PinnedSet())
    assert r3.pio_slots_evicted == 1
    assert not (base / "pio-slot-1").exists()


# ---------------------------------------------------------------------------
# host_disk_floor — runs same eviction policy under host pressure
# ---------------------------------------------------------------------------


def test_host_disk_floor_no_op_when_above_threshold(tmp_path: Path) -> None:
    """tmpfs almost always has > 10% free → no-op."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_target_cache(base, "stem-a", cache_size=300, mtime=1.0)
    result = host_disk_floor(base, min_free_pct=10, pinned=PinnedSet())
    assert result.freed_bytes == 0


def test_host_disk_floor_evicts_when_below_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    """Patch statvfs to simulate <10% free; eviction should kick in."""
    base = tmp_path
    _make_venv(base, "2026.4.3", total_size=100, mtime=10.0)
    _make_target_cache(base, "older", cache_size=300, mtime=1.0)

    # Pretend host disk is at 5% free for the entire sweep so the engine
    # walks every category, evicts everything not pinned, and returns.
    class _Vfs:
        f_frsize = 4096
        f_blocks = 1000
        f_bavail = 50  # 5% free

    monkeypatch.setattr("disk_quota.os.statvfs", lambda _p: _Vfs())
    result = host_disk_floor(base, min_free_pct=10, pinned=PinnedSet())
    # The cache is the cheapest non-venv category; it gets evicted first.
    assert not (base / "cache" / "older").exists()
    assert result.targets_evicted == 1


# ---------------------------------------------------------------------------
# ActiveJobSet — refcounted pinning context
# ---------------------------------------------------------------------------


def test_active_job_set_pin_then_unpin() -> None:
    s = ActiveJobSet()
    with s.pin("2026.4.3", "stem-a", 1):
        snap = s.snapshot()
        assert snap.venv_versions == {"2026.4.3"}
        assert snap.target_stems == {"stem-a"}
        assert snap.slot_ids == {1}
    snap = s.snapshot()
    assert snap.venv_versions == set()
    assert snap.target_stems == set()
    assert snap.slot_ids == set()


def test_active_job_set_refcount_two_jobs_same_venv() -> None:
    """Two concurrent jobs on the same venv → unpinning one keeps it pinned."""
    s = ActiveJobSet()
    with s.pin("2026.4.3", "stem-a", 1):
        with s.pin("2026.4.3", "stem-b", 2):
            assert s.snapshot().venv_versions == {"2026.4.3"}
        # Inner pin gone, outer pin still holds the venv.
        assert s.snapshot().venv_versions == {"2026.4.3"}
    assert s.snapshot().venv_versions == set()


def test_active_job_set_thread_safe() -> None:
    """Many threads pinning + unpinning in tight loops shouldn't corrupt state."""
    s = ActiveJobSet()
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            for _ in range(50):
                with s.pin(f"v{i % 3}", f"stem-{i % 5}", i % 4):
                    pass
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    snap = s.snapshot()
    # All threads done → everything unpinned.
    assert snap.venv_versions == set()
    assert snap.target_stems == set()
    assert snap.slot_ids == set()


# ---------------------------------------------------------------------------
# SweepResult helper — basic shape
# ---------------------------------------------------------------------------


def test_sweep_result_default_zero() -> None:
    r = SweepResult()
    assert r.freed_bytes == 0
    assert r.orphan_slots_evicted == 0
    assert r.venvs_evicted == 0
    assert r.targets_evicted == 0
    assert r.pio_slots_evicted == 0
