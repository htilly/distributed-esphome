"""Regression tests for finding O-001 (2026-08-27 security review).

Attacker chain this closes: the worker self-update channel
(``GET /api/v1/client/code`` → ``client._apply_update``) wrote and
``os.execv``'d server-supplied ``.py`` content authenticated only by the
shared ``server_token`` — anyone holding that token, or an on-path
attacker against the plaintext-HTTP default, could serve attacker-authored
code and get it executed as root on every worker on the next heartbeat.
These tests cover the server-side signing half (``update_signing.py``);
``test_update_signing_canonical_payload_matches.py`` covers the
server/client encoding-agreement invariant, and the client-side
verification behavior is covered directly in ``test_client.py``-adjacent
tests below.
"""

from __future__ import annotations

import base64

import pytest


@pytest.fixture(autouse=True)
def _isolated_key_path(tmp_path, monkeypatch):
    """Never touch a real /data/update_signing_key.pem from a test run."""
    import update_signing

    monkeypatch.setattr(update_signing, "_KEY_PATH", tmp_path / "update_signing_key.pem")
    monkeypatch.setattr(update_signing, "_cached_key", None)
    yield
    monkeypatch.setattr(update_signing, "_cached_key", None)


def test_get_signing_key_generates_and_persists(tmp_path):
    import update_signing

    assert not update_signing._KEY_PATH.exists()
    key1 = update_signing.get_signing_key()
    assert update_signing._KEY_PATH.exists()
    # 0600 — this file authenticates every future worker code update.
    mode = update_signing._KEY_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_get_signing_key_is_stable_across_reloads(tmp_path, monkeypatch):
    """A server restart must not silently invalidate every worker's pinned
    key — the persisted key must be the one reloaded, not a fresh one."""
    import update_signing

    key1_pub = update_signing.get_public_key_b64()

    # Simulate a fresh process: clear the in-memory cache, force a reload
    # from disk.
    monkeypatch.setattr(update_signing, "_cached_key", None)
    key2_pub = update_signing.get_public_key_b64()

    assert key1_pub == key2_pub


def test_sign_payload_verifies_with_matching_public_key():
    import update_signing
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    files = {"client.py": "print(1)\n", "version_manager.py": "print(2)\n"}
    signature_b64 = update_signing.sign_payload(files)
    pub_bytes = base64.b64decode(update_signing.get_public_key_b64())
    pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

    # Must not raise.
    pub_key.verify(
        base64.b64decode(signature_b64),
        update_signing.canonical_payload(files),
    )


def test_sign_payload_signature_does_not_verify_against_tampered_files():
    import update_signing
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    files = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(files)
    pub_bytes = base64.b64decode(update_signing.get_public_key_b64())
    pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

    tampered = {"client.py": "print('attacker-controlled')\n"}
    with pytest.raises(InvalidSignature):
        pub_key.verify(
            base64.b64decode(signature_b64),
            update_signing.canonical_payload(tampered),
        )


def test_canonical_payload_is_order_independent():
    import update_signing

    a = update_signing.canonical_payload({"b.py": "2", "a.py": "1"})
    b = update_signing.canonical_payload({"a.py": "1", "b.py": "2"})
    assert a == b


# ---------------------------------------------------------------------------
# Client-side verification (client._verify_update_signature)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _client_env(monkeypatch):
    monkeypatch.setenv("SERVER_URL", "http://localhost:8765")
    monkeypatch.setenv("SERVER_TOKEN", "test-token")


def test_verify_update_signature_accepts_valid_signature(monkeypatch):
    import update_signing
    import client as client_module  # noqa: PLC0415

    files = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(files)
    pub_key_b64 = update_signing.get_public_key_b64()

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", pub_key_b64)
    assert client_module._verify_update_signature(files, signature_b64) is True


def test_verify_update_signature_rejects_tampered_files(monkeypatch):
    import update_signing
    import client as client_module  # noqa: PLC0415

    original = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(original)
    pub_key_b64 = update_signing.get_public_key_b64()

    tampered = {"client.py": "os.system('rm -rf /')\n"}
    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", pub_key_b64)
    assert client_module._verify_update_signature(tampered, signature_b64) is False


def test_verify_update_signature_rejects_wrong_key(monkeypatch):
    """Signed correctly, but by a DIFFERENT server's key — must not verify
    against a worker pinned to a different install's key."""
    import update_signing
    import client as client_module  # noqa: PLC0415

    files = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(files)

    # A different server's key (fresh keypair, never persisted to the
    # signer's own path).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    other_key = Ed25519PrivateKey.generate()
    other_pub_b64 = base64.b64encode(
        other_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", other_pub_b64)
    assert client_module._verify_update_signature(files, signature_b64) is False


def test_verify_update_signature_rejects_missing_signature(monkeypatch):
    import update_signing
    import client as client_module  # noqa: PLC0415

    pub_key_b64 = update_signing.get_public_key_b64()
    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", pub_key_b64)
    assert client_module._verify_update_signature({"client.py": "x"}, None) is False


def test_verify_update_signature_false_when_no_key_pinned(monkeypatch):
    """No WORKER_TRUSTED_UPDATE_KEY set → verification is never even
    attempted (caller decides what to do — see _apply_update's warn-and-
    proceed fallback for unmigrated workers)."""
    import client as client_module  # noqa: PLC0415

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", None)
    assert client_module._verify_update_signature({"client.py": "x"}, "irrelevant") is False


def test_apply_update_refuses_to_write_on_bad_signature(monkeypatch, tmp_path):
    """End-to-end: a worker with a pinned key must return before it ever
    reaches the file-write loop or os.execv when the signature check
    fails — the actual sink the whole fix protects. Verification runs
    before ``client_dir`` is even computed, so a failed check can't reach
    disk regardless of where the real client files live."""
    import update_signing
    import client as client_module  # noqa: PLC0415

    pub_key_b64 = update_signing.get_public_key_b64()
    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", pub_key_b64)
    # _update_attempts is process-global state in client.py; reset it so
    # this test's outcome never depends on how many other tests in this
    # session already called _apply_update.
    monkeypatch.setattr(client_module, "_update_attempts", 0)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "version": "9.9.9-attacker",
                "files": {"client.py": "import os; os.system('pwned')\n"},
                "signature": "not-a-valid-signature-at-all==",
            }

    monkeypatch.setattr(client_module, "get", lambda *a, **kw: _FakeResp())

    write_calls = []
    monkeypatch.setattr(
        client_module.Path, "write_text",
        lambda self, *a, **kw: write_calls.append(self),
    )
    execv_calls = []
    monkeypatch.setattr(client_module.os, "execv", lambda *a: execv_calls.append(a))

    client_module._apply_update("worker-1")

    assert write_calls == []
    assert execv_calls == []
