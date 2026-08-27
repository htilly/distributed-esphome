"""Signs the worker self-update code payload (finding O-001, 2026-08-27).

Security review 2026-08-27, finding O-001: the worker self-update channel
(``GET /api/v1/client/code`` → ``client._apply_update``) used to write and
``os.execv`` server-supplied ``.py`` content with no integrity check beyond
"the shared bearer token authenticated the request" — the same channel
worker code is fetched over, authenticated only by the single shared
``server_token``. Anyone holding that token (or an on-path attacker against
the plaintext HTTP default) could serve attacker-authored code and get it
executed as root on every worker on the next heartbeat.

This module adds a detached Ed25519 signature over the code payload. The
private key is generated once per server install and never leaves this
process — it is NOT baked into any Docker image (an image is public/shared
across every install; a key inside it would not be private to anyone).
The corresponding public key is surfaced to the operator via
``GET /ui/api/server-info`` for the *Connect Worker* enrollment flow, the
same manual copy-paste channel that already hands over ``server_token`` to
a new worker — see ``ConnectWorkerModal.tsx``. This is intentionally not a
trust-on-first-use / key-pinning scheme: TOFU trusts whichever key answers
first, which does not defend against an attacker who beats the legitimate
worker to the first connection. Explicit distribution through the same
channel the token already uses adds no new trust requirement beyond what
that flow already carries.

Verification lives client-side in ``client.py`` and is opt-in per worker
via the ``WORKER_TRUSTED_UPDATE_KEY`` env var — see the note there on why
this is not made mandatory yet.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)

_KEY_PATH = Path("/data/update_signing_key.pem")

_cached_key: Ed25519PrivateKey | None = None


def _generate_and_persist() -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 0600 before writing content — this file authenticates every future
    # worker code update, so it gets the same handling as any other
    # long-lived secret material on this filesystem.
    _KEY_PATH.touch(mode=0o600, exist_ok=True)
    _KEY_PATH.chmod(0o600)
    _KEY_PATH.write_bytes(pem)
    logger.info(
        "Generated a new worker-update signing key at %s. Existing workers "
        "keep applying unsigned updates until reconnected with "
        "WORKER_TRUSTED_UPDATE_KEY (see Connect Worker) — see O-001.",
        _KEY_PATH,
    )
    return key


def get_signing_key() -> Ed25519PrivateKey:
    """Load the persisted signing key, generating one on first call.

    Cached in-process after the first successful load — this is called on
    every ``/api/v1/client/code`` response, and re-reading + re-parsing a
    PEM file on every worker poll would be pure waste (see CLAUDE.md's
    idle-efficiency guidance).
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    if _KEY_PATH.exists():
        _cached_key = serialization.load_pem_private_key(
            _KEY_PATH.read_bytes(), password=None,
        )
    else:
        _cached_key = _generate_and_persist()
    return _cached_key


def get_public_key_b64() -> str:
    """Base64-encoded raw public key — what the operator pastes into a
    worker's ``WORKER_TRUSTED_UPDATE_KEY`` env var via Connect Worker."""
    pub = get_signing_key().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(pub).decode("ascii")


def canonical_payload(files: dict[str, str]) -> bytes:
    """Deterministic byte encoding of a {filename: content} map.

    Sorted by filename so the signature does not depend on dict/glob
    iteration order — ``get_client_code`` already sorts its glob, but
    signing re-sorts independently rather than trusting the caller to have
    done so, since a future refactor of that endpoint should not silently
    invalidate every worker's verification.
    """
    parts = []
    for name in sorted(files):
        content = files[name]
        parts.append(f"{name}\0{len(content)}\0{content}")
    return "\n".join(parts).encode("utf-8")


def sign_payload(files: dict[str, str]) -> str:
    """Base64-encoded detached signature over *files*, using this server's key."""
    key = get_signing_key()
    signature = key.sign(canonical_payload(files))
    return base64.b64encode(signature).decode("ascii")
