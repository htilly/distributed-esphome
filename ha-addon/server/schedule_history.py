"""In-memory schedule history ring buffer (SU.6 / SU.7).

Stores the last N fire events per target so the UI can show a "did it
run?" debugging view without adding persistence. History survives until
server restart, which is acceptable for "did Sunday 2am fire?" questions.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

_MAX_PER_TARGET = 50

_history: dict[str, deque[tuple[datetime, str, str]]] = {}


def record(target: str, fired_at: datetime, job_id: str, outcome: str = "enqueued") -> None:
    """Record a scheduler fire event."""
    _history.setdefault(target, deque(maxlen=_MAX_PER_TARGET)).append(
        (fired_at, job_id, outcome)
    )


def update_outcome(job_id: str, outcome: str) -> None:
    """Update the outcome of a previously recorded fire event by job_id."""
    for entries in _history.values():
        for i, (fired_at, jid, _old_outcome) in enumerate(entries):
            if jid == job_id:
                entries[i] = (fired_at, jid, outcome)
                return


def get(target: str) -> list[tuple[datetime, str, str]]:
    """Return fire history for a target as [(fired_at, job_id, outcome), ...]."""
    return list(_history.get(target, []))


def get_all() -> dict[str, list[tuple[datetime, str, str]]]:
    """Return all history entries."""
    return {k: list(v) for k, v in _history.items()}


def clear() -> None:
    """Clear all history (used in tests)."""
    _history.clear()
