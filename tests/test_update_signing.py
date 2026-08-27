"""Regression tests for finding O-001 (2026-08-27 security review) and its
same-day trust-on-first-update (TOFU) follow-up.

Attacker chain this closes: the worker self-update channel
(``GET /api/v1/client/code`` → ``client._apply_update``) wrote and
``os.execv``'d server-supplied ``.py`` content authenticated only by the
shared ``server_token`` — anyone holding that token, or an on-path
attacker against the plaintext-HTTP default, could serve attacker-authored
code and get it executed as root on every worker on the next heartbeat.

The initial fix made verification opt-in via ``WORKER_TRUSTED_UPDATE_KEY``,
which meant no existing worker was ever protected unless an operator
manually reconnected it. The follow-up covered here makes protection
automatic: a worker with no key pinned yet trusts its next update once
(identical to pre-fix behavior — no regression) and pins the key it was
given, so every update after that is verified with no operator action.

These tests cover the server-side signing half (``update_signing.py``);
``test_update_signing_canonical_payload_matches.py`` covers the
server/client encoding-agreement invariant; the client-side verification
and TOFU-pinning behavior is covered directly below.
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
# Client-side verification (client._verify_update_signature — pure verifier)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _client_env(monkeypatch):
    monkeypatch.setenv("SERVER_URL", "http://localhost:8765")
    monkeypatch.setenv("SERVER_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def _isolated_trusted_key_file(tmp_path, monkeypatch):
    """_trusted_update_key_path() resolves next to client.py's real
    __file__ by default — never let a test touch the actual
    ha-addon/client/.trusted_update_key on disk."""
    import client as client_module  # noqa: PLC0415

    fake_path = tmp_path / ".trusted_update_key"
    monkeypatch.setattr(client_module, "_trusted_update_key_path", lambda: fake_path)
    return fake_path


def test_verify_update_signature_accepts_valid_signature():
    import update_signing
    import client as client_module  # noqa: PLC0415

    files = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(files)
    pub_key_b64 = update_signing.get_public_key_b64()

    assert client_module._verify_update_signature(files, signature_b64, pub_key_b64) is True


def test_verify_update_signature_rejects_tampered_files():
    import update_signing
    import client as client_module  # noqa: PLC0415

    original = {"client.py": "print(1)\n"}
    signature_b64 = update_signing.sign_payload(original)
    pub_key_b64 = update_signing.get_public_key_b64()

    tampered = {"client.py": "os.system('rm -rf /')\n"}
    assert client_module._verify_update_signature(tampered, signature_b64, pub_key_b64) is False


def test_verify_update_signature_rejects_wrong_key():
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

    assert client_module._verify_update_signature(files, signature_b64, other_pub_b64) is False


def test_verify_update_signature_rejects_missing_signature():
    import update_signing
    import client as client_module  # noqa: PLC0415

    pub_key_b64 = update_signing.get_public_key_b64()
    assert client_module._verify_update_signature({"client.py": "x"}, None, pub_key_b64) is False


def test_verify_update_signature_false_when_no_key_given():
    import client as client_module  # noqa: PLC0415

    assert client_module._verify_update_signature({"client.py": "x"}, "irrelevant", None) is False


# ---------------------------------------------------------------------------
# Key resolution + TOFU persistence (client._load_pinned_update_key /
# client._persist_pinned_update_key)
# ---------------------------------------------------------------------------

def test_load_pinned_update_key_returns_none_when_nothing_pinned():
    import client as client_module  # noqa: PLC0415

    assert client_module._load_pinned_update_key() is None


def test_load_pinned_update_key_env_var_wins_when_set(monkeypatch):
    import client as client_module  # noqa: PLC0415

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", "env-key-value")
    # Even with a persisted file present, the env var must win.
    client_module._trusted_update_key_path().write_text("file-key-value")
    assert client_module._load_pinned_update_key() == "env-key-value"


def test_persist_then_load_pinned_update_key(_isolated_trusted_key_file):
    import client as client_module  # noqa: PLC0415

    client_module._persist_pinned_update_key("some-b64-key")
    assert _isolated_trusted_key_file.read_text() == "some-b64-key"
    mode = _isolated_trusted_key_file.stat().st_mode & 0o777
    assert mode == 0o600
    assert client_module._load_pinned_update_key() == "some-b64-key"


def test_persist_pinned_update_key_failure_does_not_raise(monkeypatch):
    """Best-effort: a write failure must not propagate and block the
    (already-trusted-this-once) update from being applied."""
    import client as client_module  # noqa: PLC0415

    def _boom():
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(client_module, "_trusted_update_key_path", _boom)
    client_module._persist_pinned_update_key("some-b64-key")  # must not raise


# ---------------------------------------------------------------------------
# End-to-end _apply_update: the three-branch decision + the actual sink
# ---------------------------------------------------------------------------

def _reset_update_attempts(monkeypatch, client_module):
    """_update_attempts is process-global state in client.py; reset it so
    a test's outcome never depends on how many other tests in this
    session already called _apply_update."""
    monkeypatch.setattr(client_module, "_update_attempts", 0)


def test_apply_update_refuses_to_write_on_bad_signature(monkeypatch):
    """A worker with a pinned key must return before it ever reaches the
    file-write loop or os.execv when the signature check fails — the
    actual sink the whole fix protects."""
    import update_signing
    import client as client_module  # noqa: PLC0415

    pub_key_b64 = update_signing.get_public_key_b64()
    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", pub_key_b64)
    _reset_update_attempts(monkeypatch, client_module)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "version": "9.9.9-attacker",
                "files": {"client.py": "import os; os.system('pwned')\n"},
                "signature": "not-a-valid-signature-at-all==",
                "update_signing_public_key": pub_key_b64,
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


def test_apply_update_bootstraps_tofu_pin_on_first_update(monkeypatch, tmp_path, _isolated_trusted_key_file):
    """The core of the follow-up fix: a worker with NO key pinned yet
    (env var unset, no persisted file) must still apply an update whose
    signature it cannot check — same as pre-O-001 behavior, not a
    regression — and must come out the other side having pinned the
    server's offered key for next time.

    Redirects client_dir (via __file__) to a scratch directory so both
    the real .py write AND the real key-pin write happen for real,
    against throwaway paths — rather than faking Path.write_text, which
    would also swallow _persist_pinned_update_key's own write and make
    this test unable to observe the thing it's testing.
    """
    import update_signing
    import client as client_module  # noqa: PLC0415

    scratch_dir = tmp_path / "client_dir"
    scratch_dir.mkdir()
    monkeypatch.setattr(client_module, "__file__", str(scratch_dir / "client.py"))

    pub_key_b64 = update_signing.get_public_key_b64()
    files = {"client.py": "print('legit update')\n"}
    signature_b64 = update_signing.sign_payload(files)

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", None)
    _reset_update_attempts(monkeypatch, client_module)
    assert client_module._load_pinned_update_key() is None  # precondition

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "version": "1.0.0",
                "files": files,
                "signature": signature_b64,
                "update_signing_public_key": pub_key_b64,
            }

    monkeypatch.setattr(client_module, "get", lambda *a, **kw: _FakeResp())
    execv_calls = []
    monkeypatch.setattr(client_module.os, "execv", lambda *a: execv_calls.append(a))

    client_module._apply_update("worker-1")

    # The update was applied (execv reached) even though nothing was
    # pinned yet to verify it against...
    assert len(execv_calls) == 1
    assert (scratch_dir / "client.py").read_text() == files["client.py"]
    # ...and the key offered in this response is now pinned for next time.
    assert client_module._load_pinned_update_key() == pub_key_b64


def test_apply_update_second_update_after_bootstrap_requires_valid_signature(monkeypatch, _isolated_trusted_key_file):
    """Proves the bootstrap pin actually bites: once a key is pinned (by
    the previous test's scenario, reproduced here), a SUBSEQUENT update
    with a bad signature must be refused, not silently trusted again."""
    import update_signing
    import client as client_module  # noqa: PLC0415

    pub_key_b64 = update_signing.get_public_key_b64()
    # Simulate: this worker already bootstrapped and pinned the key.
    client_module._persist_pinned_update_key(pub_key_b64)
    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", None)
    _reset_update_attempts(monkeypatch, client_module)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "version": "2.0.0-attacker",
                "files": {"client.py": "import os; os.system('pwned')\n"},
                "signature": "not-a-valid-signature-at-all==",
                "update_signing_public_key": pub_key_b64,
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


def test_apply_update_unsigned_when_server_offers_no_key(monkeypatch, _isolated_trusted_key_file):
    """A pre-O-001 server (no update_signing_public_key in the response
    at all) must still work exactly as before this fix existed — no
    key to pin, no verification possible, update still applies."""
    import client as client_module  # noqa: PLC0415

    monkeypatch.setattr(client_module, "WORKER_TRUSTED_UPDATE_KEY", None)
    _reset_update_attempts(monkeypatch, client_module)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "version": "1.0.0",
                "files": {"client.py": "print('legit')\n"},
                # no "signature" / "update_signing_public_key" at all
            }

    monkeypatch.setattr(client_module, "get", lambda *a, **kw: _FakeResp())
    monkeypatch.setattr(client_module.Path, "write_text", lambda self, *a, **kw: None)
    execv_calls = []
    monkeypatch.setattr(client_module.os, "execv", lambda *a: execv_calls.append(a))

    client_module._apply_update("worker-1")

    assert len(execv_calls) == 1
    # Nothing to pin — still unpinned afterward.
    assert client_module._load_pinned_update_key() is None
