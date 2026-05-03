"""FD.5/FD.6/FD.7 + #69 + #198 unit tests — firmware storage lifecycle."""

from __future__ import annotations

import os
import time
from pathlib import Path

from firmware_storage import (
    delete_firmware,
    enforce_retention,
    firmware_path,
    list_variants,
    read_firmware,
    reconcile_orphans,
    save_firmware,
)


def test_save_creates_directory_and_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    assert not dest.exists()
    # Default variant is "factory" post-#69 — on ESP32 this is the
    # full flash image written at `{job_id}.factory.bin`.
    path = save_firmware("job-1", b"hello", root=dest)
    assert path.read_bytes() == b"hello"
    assert path == dest / "job-1.factory.bin"


def test_save_overwrites_existing(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    save_firmware("job-1", b"first", root=dest)
    save_firmware("job-1", b"second", root=dest)
    assert (dest / "job-1.factory.bin").read_bytes() == b"second"


def test_save_writes_each_variant_independently(tmp_path: Path) -> None:
    """#69 — factory + ota live side by side under the same job id."""
    save_firmware("job-1", b"factory-blob", variant="factory", root=tmp_path)
    save_firmware("job-1", b"ota-blob", variant="ota", root=tmp_path)
    assert (tmp_path / "job-1.factory.bin").read_bytes() == b"factory-blob"
    assert (tmp_path / "job-1.ota.bin").read_bytes() == b"ota-blob"


def test_delete_removes_all_variants(tmp_path: Path) -> None:
    """#69 — user Clear must wipe every variant, not just one."""
    save_firmware("job-1", b"a", variant="factory", root=tmp_path)
    save_firmware("job-1", b"b", variant="ota", root=tmp_path)
    assert delete_firmware("job-1", root=tmp_path) is True
    assert not (tmp_path / "job-1.factory.bin").exists()
    assert not (tmp_path / "job-1.ota.bin").exists()


def test_delete_returns_false_when_missing(tmp_path: Path) -> None:
    assert delete_firmware("never-existed", root=tmp_path) is False


def test_delete_cleans_up_legacy_pre_69_blob(tmp_path: Path) -> None:
    """Pre-#69 installs have ``{job_id}.bin`` on disk; upgrade Clear
    must still remove those rather than stranding bytes forever."""
    legacy = tmp_path / "legacy-job.bin"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"old")
    assert delete_firmware("legacy-job", root=tmp_path) is True
    assert not legacy.exists()


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_firmware("missing", root=tmp_path) is None


def test_read_returns_bytes_when_present(tmp_path: Path) -> None:
    save_firmware("job-1", b"bytes", variant="ota", root=tmp_path)
    assert read_firmware("job-1", variant="ota", root=tmp_path) == b"bytes"
    # Unmatched variant returns None — callers should 404, not silently
    # swap in another variant.
    assert read_firmware("job-1", variant="factory", root=tmp_path) is None


def test_firmware_path_uses_variant_suffix(tmp_path: Path) -> None:
    # Default (factory) — post-#69 shape.
    assert firmware_path("abc-123", root=tmp_path) == tmp_path / "abc-123.factory.bin"
    assert (
        firmware_path("abc-123", variant="ota", root=tmp_path)
        == tmp_path / "abc-123.ota.bin"
    )
    # Synthetic "firmware" variant resolves back to the pre-#69 layout
    # so upgraded installs keep reading old blobs.
    assert (
        firmware_path("abc-123", variant="firmware", root=tmp_path)
        == tmp_path / "abc-123.bin"
    )


def test_list_variants_orders_factory_before_ota(tmp_path: Path) -> None:
    save_firmware("job-1", b"o", variant="ota", root=tmp_path)
    save_firmware("job-1", b"f", variant="factory", root=tmp_path)
    assert list_variants("job-1", root=tmp_path) == ["factory", "ota"]


def test_list_variants_empty_when_none_stored(tmp_path: Path) -> None:
    assert list_variants("nobody-home", root=tmp_path) == []


def test_list_variants_exposes_legacy_blob_as_firmware(tmp_path: Path) -> None:
    """Pre-#69 on-disk `{job_id}.bin` surfaces as variant "firmware"
    so the UI's Download dropdown still offers it after an upgrade."""
    (tmp_path / "legacy-job.bin").write_bytes(b"old")
    assert list_variants("legacy-job", root=tmp_path) == ["firmware"]


def test_reconcile_removes_orphans_keeps_active(tmp_path: Path) -> None:
    save_firmware("keep", b"a", variant="factory", root=tmp_path)
    save_firmware("keep", b"b", variant="ota", root=tmp_path)
    save_firmware("drop1", b"c", variant="factory", root=tmp_path)
    save_firmware("drop2", b"d", variant="ota", root=tmp_path)
    removed = reconcile_orphans(["keep"], root=tmp_path)
    assert removed == 2  # drop1.factory.bin, drop2.ota.bin
    assert (tmp_path / "keep.factory.bin").exists()
    assert (tmp_path / "keep.ota.bin").exists()
    assert not (tmp_path / "drop1.factory.bin").exists()
    assert not (tmp_path / "drop2.ota.bin").exists()


def test_reconcile_sweeps_pre_69_legacy_layout(tmp_path: Path) -> None:
    """Pre-#69 `{job_id}.bin` files are still swept by reconcile_orphans."""
    (tmp_path / "keep.bin").write_bytes(b"keep-me")
    (tmp_path / "drop.bin").write_bytes(b"drop-me")
    removed = reconcile_orphans(["keep"], root=tmp_path)
    assert removed == 1
    assert (tmp_path / "keep.bin").exists()
    assert not (tmp_path / "drop.bin").exists()


def test_reconcile_no_op_when_directory_missing(tmp_path: Path) -> None:
    assert reconcile_orphans(["x"], root=tmp_path / "nope") == 0


def test_reconcile_ignores_non_bin_files(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    save_firmware("keep", b"a", variant="factory", root=tmp_path)
    (tmp_path / "readme.txt").write_text("don't delete me")
    removed = reconcile_orphans(["keep"], root=tmp_path)
    assert removed == 0
    assert (tmp_path / "readme.txt").exists()


# ---------------------------------------------------------------------------
# Bug #198: time-based retention
# ---------------------------------------------------------------------------


def _set_mtime(path: Path, age_seconds: float) -> None:
    """Make *path* look like it was last modified *age_seconds* ago."""
    when = time.time() - age_seconds
    os.utime(path, (when, when))


def test_enforce_retention_evicts_files_older_than_cutoff(tmp_path: Path) -> None:
    """Files past the age cutoff are deleted; fresh files are kept."""
    save_firmware("old", b"x", variant="factory", root=tmp_path)
    save_firmware("new", b"y", variant="factory", root=tmp_path)
    _set_mtime(tmp_path / "old.factory.bin", age_seconds=3 * 86400)
    _set_mtime(tmp_path / "new.factory.bin", age_seconds=60)

    removed = enforce_retention(max_age_seconds=2 * 86400, root=tmp_path)
    assert removed == 1
    assert not (tmp_path / "old.factory.bin").exists()
    assert (tmp_path / "new.factory.bin").exists()


def test_enforce_retention_protects_live_queue_jobs(tmp_path: Path) -> None:
    """Even if a job's binary is past the cutoff, it stays if the job
    is still in the live queue (active compile / pending OTA)."""
    save_firmware("queued", b"x", variant="factory", root=tmp_path)
    save_firmware("forgotten", b"y", variant="factory", root=tmp_path)
    _set_mtime(tmp_path / "queued.factory.bin", age_seconds=10 * 86400)
    _set_mtime(tmp_path / "forgotten.factory.bin", age_seconds=10 * 86400)

    removed = enforce_retention(
        max_age_seconds=2 * 86400,
        protected_job_ids=["queued"],
        root=tmp_path,
    )
    assert removed == 1
    assert (tmp_path / "queued.factory.bin").exists()
    assert not (tmp_path / "forgotten.factory.bin").exists()


def test_enforce_retention_evicts_history_protected_binaries(tmp_path: Path) -> None:
    """Bug #198's behavioural contract: history-protected binaries
    are NOT exempt from time-based retention. The whole point of this
    pass is to bound the on-disk firmware footprint regardless of
    history retention.
    """
    # Caller (main.py) only passes the live-queue protected set into
    # enforce_retention — history is intentionally absent here.
    save_firmware("from-history", b"x", variant="factory", root=tmp_path)
    _set_mtime(tmp_path / "from-history.factory.bin", age_seconds=5 * 86400)
    removed = enforce_retention(max_age_seconds=2 * 86400, root=tmp_path)
    assert removed == 1


def test_enforce_retention_zero_or_negative_is_noop(tmp_path: Path) -> None:
    """``max_age_seconds <= 0`` means "no retention" — same convention
    as the other retention knobs (job_history, job_log)."""
    save_firmware("ancient", b"x", variant="factory", root=tmp_path)
    _set_mtime(tmp_path / "ancient.factory.bin", age_seconds=365 * 86400)
    assert enforce_retention(max_age_seconds=0, root=tmp_path) == 0
    assert enforce_retention(max_age_seconds=-1, root=tmp_path) == 0
    assert (tmp_path / "ancient.factory.bin").exists()


def test_enforce_retention_no_op_when_directory_missing(tmp_path: Path) -> None:
    """No directory at all → return 0, don't crash."""
    assert enforce_retention(max_age_seconds=86400, root=tmp_path / "nope") == 0


def test_enforce_retention_walks_both_variants_per_job(tmp_path: Path) -> None:
    """A job with both factory + ota variants past the cutoff: both
    get evicted (each `.bin` evaluated independently on its own mtime).
    """
    save_firmware("dual", b"f", variant="factory", root=tmp_path)
    save_firmware("dual", b"o", variant="ota", root=tmp_path)
    _set_mtime(tmp_path / "dual.factory.bin", age_seconds=5 * 86400)
    _set_mtime(tmp_path / "dual.ota.bin", age_seconds=5 * 86400)
    removed = enforce_retention(max_age_seconds=2 * 86400, root=tmp_path)
    assert removed == 2
    assert list_variants("dual", root=tmp_path) == []
