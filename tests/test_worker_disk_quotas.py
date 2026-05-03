"""Tests for DQ.2 — per-worker disk-quota override persistence.

Mirrors the shape of ``tests/test_worker_tags.py``: hostname-keyed
JSON store, seed-on-first-registration semantics, server-side-wins
on re-registration, atomic JSON round-trip.

The semantic difference from worker-tags is the value type: an
override is ``int | None`` rather than ``list[str]``. ``None`` is
a meaningful value (clear the override → inherit the fleet default)
which is distinct from "absent" only at the API layer; the store
treats them the same on read (``get_quota`` returns ``None`` for
both).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker_disk_quotas import WorkerDiskQuotaStore


@pytest.fixture
def store(tmp_path: Path) -> WorkerDiskQuotaStore:
    return WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")


# ---------------------------------------------------------------------------
# load_or_seed — first-time seed vs. server-side wins
# ---------------------------------------------------------------------------


def test_load_or_seed_first_time_persists_quota(
    store: WorkerDiskQuotaStore, tmp_path: Path
) -> None:
    quota = store.load_or_seed("host-1", 5 * 1024 ** 3)
    assert quota == 5 * 1024 ** 3
    saved = json.loads((tmp_path / "worker-disk-quotas.json").read_text())
    assert saved["quotas"]["host-1"] == 5 * 1024 ** 3


def test_load_or_seed_server_side_wins_after_first_registration(
    store: WorkerDiskQuotaStore,
) -> None:
    store.load_or_seed("host-1", 5 * 1024 ** 3)
    # Worker re-registers later with a different env — server keeps the original.
    quota = store.load_or_seed("host-1", 20 * 1024 ** 3)
    assert quota == 5 * 1024 ** 3


def test_load_or_seed_first_time_with_no_quota_persists_none(
    store: WorkerDiskQuotaStore,
) -> None:
    """Worker without ``WORKER_DISK_QUOTA_GB`` env on first reg → None, persisted."""
    quota = store.load_or_seed("host-1", None)
    assert quota is None
    # An entry now exists for host-1 — subsequent reg with a value doesn't reseed.
    quota2 = store.load_or_seed("host-1", 5 * 1024 ** 3)
    assert quota2 is None


def test_load_or_seed_existing_entry_no_payload_keeps_existing(
    store: WorkerDiskQuotaStore,
) -> None:
    store.load_or_seed("host-1", 5 * 1024 ** 3)
    # Worker re-registers without sending the override (older worker version).
    quota = store.load_or_seed("host-1", None)
    assert quota == 5 * 1024 ** 3


# ---------------------------------------------------------------------------
# Persistence + recovery
# ---------------------------------------------------------------------------


def test_persistence_across_instances(tmp_path: Path) -> None:
    s1 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    s1.load_or_seed("host-1", 5 * 1024 ** 3)

    s2 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    assert s2.get_quota("host-1") == 5 * 1024 ** 3


def test_persistence_round_trips_explicit_none(tmp_path: Path) -> None:
    """An explicit None (no override) round-trips through disk."""
    s1 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    s1.load_or_seed("host-1", None)
    s2 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    # The entry exists, but its value is None (distinct from "no entry").
    assert s2.get_quota("host-1") is None
    # And the next re-registration with a seed still returns the persisted None.
    assert s2.load_or_seed("host-1", 5 * 1024 ** 3) is None


def test_corrupt_file_yields_empty_store(tmp_path: Path) -> None:
    path = tmp_path / "worker-disk-quotas.json"
    path.write_text("{not valid json")
    s = WorkerDiskQuotaStore(path=path)
    assert s.get_quota("anyone") is None
    # Recovery still allows new seeds.
    assert s.load_or_seed("host-1", 5 * 1024 ** 3) == 5 * 1024 ** 3


def test_missing_file_yields_empty_store(tmp_path: Path) -> None:
    s = WorkerDiskQuotaStore(path=tmp_path / "does-not-exist.json")
    assert s.get_quota("anyone") is None


def test_unknown_schema_version_resets_safely(tmp_path: Path) -> None:
    path = tmp_path / "worker-disk-quotas.json"
    path.write_text(
        json.dumps({"version": 999, "quotas": {"host-1": 5 * 1024 ** 3}})
    )
    s = WorkerDiskQuotaStore(path=path)
    assert s.get_quota("host-1") is None


def test_garbage_value_in_file_is_dropped(tmp_path: Path) -> None:
    """Hand-edited file with a malformed entry shouldn't poison the rest."""
    path = tmp_path / "worker-disk-quotas.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "quotas": {
                "host-1": 5 * 1024 ** 3,
                "host-2": "not an integer",
                "host-3": -5,  # negative — silently dropped
                "host-4": None,
            },
        })
    )
    s = WorkerDiskQuotaStore(path=path)
    assert s.get_quota("host-1") == 5 * 1024 ** 3
    assert s.get_quota("host-2") is None  # garbage dropped
    assert s.get_quota("host-3") is None  # negative dropped
    assert s.get_quota("host-4") is None  # explicit null preserved


# ---------------------------------------------------------------------------
# set_quota (authoritative UI edit path)
# ---------------------------------------------------------------------------


def test_set_quota_authoritative(store: WorkerDiskQuotaStore) -> None:
    store.load_or_seed("host-1", 5 * 1024 ** 3)
    result = store.set_quota("host-1", 20 * 1024 ** 3)
    assert result == 20 * 1024 ** 3
    assert store.get_quota("host-1") == 20 * 1024 ** 3


def test_set_quota_to_none_clears_override(store: WorkerDiskQuotaStore) -> None:
    store.load_or_seed("host-1", 5 * 1024 ** 3)
    result = store.set_quota("host-1", None)
    assert result is None
    assert store.get_quota("host-1") is None


def test_set_quota_persists_across_instances(tmp_path: Path) -> None:
    s1 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    s1.set_quota("host-1", 5 * 1024 ** 3)
    s2 = WorkerDiskQuotaStore(path=tmp_path / "worker-disk-quotas.json")
    assert s2.get_quota("host-1") == 5 * 1024 ** 3
