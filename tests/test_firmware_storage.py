"""FD.5/FD.6/FD.7 unit tests — firmware storage lifecycle."""

from __future__ import annotations

from pathlib import Path

from firmware_storage import (
    delete_firmware,
    firmware_path,
    read_firmware,
    reconcile_orphans,
    save_firmware,
)


def test_save_creates_directory_and_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    assert not dest.exists()
    path = save_firmware("job-1", b"hello", root=dest)
    assert path.read_bytes() == b"hello"
    assert path == dest / "job-1.bin"


def test_save_overwrites_existing(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    save_firmware("job-1", b"first", root=dest)
    save_firmware("job-1", b"second", root=dest)
    assert (dest / "job-1.bin").read_bytes() == b"second"


def test_delete_returns_true_when_present(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    save_firmware("job-1", b"x", root=dest)
    assert delete_firmware("job-1", root=dest) is True
    assert not (dest / "job-1.bin").exists()


def test_delete_returns_false_when_missing(tmp_path: Path) -> None:
    dest = tmp_path / "firmware"
    assert delete_firmware("never-existed", root=dest) is False


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_firmware("missing", root=tmp_path) is None


def test_read_returns_bytes_when_present(tmp_path: Path) -> None:
    save_firmware("job-1", b"bytes", root=tmp_path)
    assert read_firmware("job-1", root=tmp_path) == b"bytes"


def test_firmware_path_uses_job_id_and_bin_suffix(tmp_path: Path) -> None:
    assert firmware_path("abc-123", root=tmp_path) == tmp_path / "abc-123.bin"


def test_reconcile_removes_orphans_keeps_active(tmp_path: Path) -> None:
    save_firmware("keep", b"a", root=tmp_path)
    save_firmware("drop1", b"b", root=tmp_path)
    save_firmware("drop2", b"c", root=tmp_path)
    removed = reconcile_orphans(["keep"], root=tmp_path)
    assert removed == 2
    assert (tmp_path / "keep.bin").exists()
    assert not (tmp_path / "drop1.bin").exists()
    assert not (tmp_path / "drop2.bin").exists()


def test_reconcile_no_op_when_directory_missing(tmp_path: Path) -> None:
    assert reconcile_orphans(["x"], root=tmp_path / "nope") == 0


def test_reconcile_ignores_non_bin_files(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "keep.bin").write_bytes(b"a")
    (tmp_path / "readme.txt").write_text("don't delete me")
    removed = reconcile_orphans(["keep"], root=tmp_path)
    assert removed == 0
    assert (tmp_path / "readme.txt").exists()
