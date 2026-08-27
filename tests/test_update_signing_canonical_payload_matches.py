"""O-001 (2026-08-27 security review): the server signs a worker code
payload with ``update_signing.canonical_payload()`` and the worker
verifies it with ``client._canonical_update_payload()``. These two
functions live in different files (server vs. client) and can't be the
same byte-identical file the way ``protocol.py`` is (see
``test_protocol.py``), so this test pins their *behavior* instead: given
the same input, they must produce the exact same bytes, or every
signature verification silently fails and every worker falls back to
unsigned updates — quietly reopening O-001 on the next refactor of either
copy.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_required_env(monkeypatch):
    # client.py reads SERVER_URL / SERVER_TOKEN at import time.
    monkeypatch.setenv("SERVER_URL", "http://localhost:8765")
    monkeypatch.setenv("SERVER_TOKEN", "test-token")


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"client.py": "print('hello')\n"},
        {"client.py": "a\n", "version_manager.py": "b\n", "protocol.py": "c\n"},
        # Order in the dict must not matter — both sides sort by filename.
        {"z_module.py": "last\n", "a_module.py": "first\n"},
        # Content containing the separator bytes this encoding relies on
        # must not desynchronize the two implementations.
        {"weird.py": "contains\0null\0bytes\nand\nnewlines\n"},
    ],
)
def test_canonical_payload_matches_between_server_and_client(files):
    import update_signing  # server copy
    import client as client_module  # client copy  # noqa: PLC0415

    server_bytes = update_signing.canonical_payload(files)
    client_bytes = client_module._canonical_update_payload(files)

    assert server_bytes == client_bytes, (
        "update_signing.canonical_payload() and "
        "client._canonical_update_payload() have diverged for input "
        f"{files!r} — every worker signature verification would now fail "
        "silently. Keep both implementations byte-identical in behavior."
    )
