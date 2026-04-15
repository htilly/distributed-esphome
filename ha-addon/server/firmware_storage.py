"""Per-queue-item firmware storage (FD.5/FD.6/FD.7).

Binaries produced by download-only jobs land at
``/data/firmware/{job_id}.bin``. Lifecycle is coupled to the queue
entry: when a job is removed from the queue (user Clear, bulk clear,
per-target coalescing cleanup, startup orphan sweep), the matching
.bin is deleted. No time-based cleanup — consistent with bug #18's
"users clear explicitly" stance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# Default storage root — `/data/` is persisted by HA across add-on
# updates/restarts/rebuilds. Override via the argument to each helper
# so tests can use tmp_path.
DEFAULT_FIRMWARE_DIR = Path("/data/firmware")


def _resolve_root(root: Optional[Path]) -> Path:
    """Resolve the storage root, honoring a runtime override of
    ``DEFAULT_FIRMWARE_DIR`` via monkeypatch (used by pytest).

    Reading the module attribute at call time (instead of binding the
    default at function-definition time) lets tests flip the root
    without touching each helper signature.
    """
    import firmware_storage as _fs  # noqa: PLC0415 — self-import is deliberate
    return root if root is not None else _fs.DEFAULT_FIRMWARE_DIR


def firmware_path(job_id: str, root: Optional[Path] = None) -> Path:
    """Return the canonical `.bin` path for *job_id* under *root*."""
    return _resolve_root(root) / f"{job_id}.bin"


def save_firmware(job_id: str, data: bytes, root: Optional[Path] = None) -> Path:
    """Persist *data* as the binary for *job_id*. Returns the written path.

    Overwrites in place — retry of the same job re-uploads atop the
    previous binary (acceptable; the server's has_firmware flag is
    already True and we re-flip it either way).
    """
    r = _resolve_root(root)
    r.mkdir(parents=True, exist_ok=True)
    path = firmware_path(job_id, r)
    path.write_bytes(data)
    logger.info("Stored firmware for job %s at %s (%d bytes)", job_id, path, len(data))
    return path


def delete_firmware(job_id: str, root: Optional[Path] = None) -> bool:
    """Remove the stored binary for *job_id*. Returns True if a file was deleted."""
    path = firmware_path(job_id, _resolve_root(root))
    try:
        path.unlink()
        logger.info("Deleted firmware for job %s (%s)", job_id, path)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("Failed to delete firmware for job %s at %s", job_id, path)
        return False


def read_firmware(job_id: str, root: Optional[Path] = None) -> Optional[bytes]:
    """Return the stored binary for *job_id*, or None if missing."""
    path = firmware_path(job_id, _resolve_root(root))
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def reconcile_orphans(active_job_ids: Iterable[str], root: Optional[Path] = None) -> int:
    """Delete any `.bin` in *root* whose job is no longer active.

    Called once at server startup: the queue file is the source of
    truth for what's alive, so anything on disk not in that set is
    stale (e.g. add-on was killed mid-cleanup on a previous run).
    Returns the number of files removed.
    """
    r = _resolve_root(root)
    try:
        if not r.is_dir():
            return 0
    except Exception:
        return 0
    active = set(active_job_ids)
    removed = 0
    for entry in r.iterdir():
        if not entry.is_file() or entry.suffix != ".bin":
            continue
        job_id = entry.stem
        if job_id in active:
            continue
        try:
            entry.unlink()
            removed += 1
        except Exception:
            logger.debug("Couldn't remove orphan firmware %s", entry, exc_info=True)
    if removed:
        logger.info("Reconciled %d orphan firmware file(s) in %s", removed, r)
    return removed
