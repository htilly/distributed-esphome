# Security Policy

## Supported Versions

| Version  | Supported          |
|----------|--------------------|
| 1.4.x    | ✅ Current release  |
| 1.3.1    | ✅ Previous stable — security fixes only if trivially backportable |
| < 1.3.1  | ❌ No patches       |

## Reporting a Vulnerability

If you discover a security vulnerability, please [open a GitHub issue](https://github.com/weirded/distributed-esphome/issues/new) with:

- A description of the vulnerability
- Steps to reproduce
- The affected version(s)
- Any suggested fix (optional but appreciated)

For vulnerabilities you'd prefer not to disclose publicly, open a minimal placeholder issue asking for a private contact channel and the maintainer will follow up.

## Threat Model

This project's security posture is documented in [`dev-plans/SECURITY_AUDIT.md`](dev-plans/SECURITY_AUDIT.md), including:

- A supply chain threat model (9 prioritized vectors, with current mitigation state)
- An OWASP Top 10 (2021) assessment
- 20 individual findings (F-01 through F-20) with severity ratings and current status
- A "Post-audit mitigations" summary of everything shipped since the original 2026-03-29 audit

The stated threat model is a **trusted home network** behind Home Assistant's Ingress authentication. The server add-on relies on HA Ingress for UI authentication and a shared Bearer token for worker authentication. See the audit document for the full analysis and accepted risks.

## Security Measures

### Supply chain

- **Hash-pinned Python dependencies** (`--require-hashes`) in both server and client Docker images. Lockfiles regenerated via `scripts/refresh-deps.sh`.
- **`pip-audit` + `npm audit`** gating CI on every push — hard failures block merge.
- **Dependabot** configured for pip × 2 (server + client), npm, docker × 2, and github-actions (weekly).
- **Cosign-signed GHCR images** (keyless / GitHub OIDC) — verify with:
  ```bash
  cosign verify \
    --certificate-identity-regexp 'https://github.com/weirded/distributed-esphome/.github/workflows/publish-.*\.yml@.*' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    ghcr.io/weirded/esphome-dist-client:latest
  ```
- **PY-7 invariant** — every `--ignore-vuln` in `pip-audit` must carry an inline applicability assessment (why the fix can't be pulled in, whether our code exercises the vulnerable path, dated). Prevents silent CVE dismissals.
- **PY-8 invariant** — every direct dep in `requirements.txt` must also appear in `requirements.lock`. Enforced by `scripts/check-invariants.sh` so a forgotten `refresh-deps.sh` fails CI instead of shipping a broken image.

### Web surface

- **Security response headers** (CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `X-Frame-Options: SAMEORIGIN`) on every UI response via a dedicated aiohttp middleware. Deliberately not applied to the `/api/v1/*` worker tier.
- **Path traversal prevention** — all file-endpoint handlers route through `helpers.safe_resolve()`.
- **Monaco editor bundled via Vite** — no external CDN, eliminates a supply-chain vector and enables offline/air-gapped HA installations.

### Protocol & validation

- **Typed protocol** (pydantic v2) with structured `ProtocolError` responses on malformed payloads. `PROTOCOL_VERSION` gate rejects mismatched peers with a clear error.
- **Byte-identical `protocol.py`** between server and client, enforced by `tests/test_protocol.py::test_server_and_client_protocol_files_are_identical` — prevents wire-contract drift.
- **Log payload DoS guard** — `/api/v1/jobs/{id}/log` rejects bodies larger than ~2MB (`log_payload_too_large` → HTTP 413) before aiohttp buffers the full input.

### Auth / observability

- **Structured 401 reasons** (`missing_authorization_header`, `authorization_not_bearer_scheme`, `bearer_token_mismatch`) logged at WARNING with the peer IP for every worker-tier auth refusal.
- **IPv6-aware peer IP normalization** — IPv6 zone IDs stripped, IPv4-mapped IPv6 unwrapped, `peername=None` handled without crashing.

### What is *not* in scope

These are accepted risks within the home-network threat model; see the full audit for rationale:

- **HTTP between workers and server** (not HTTPS). Users with remote workers across network segments should front the server with their own reverse proxy.
- **Bearer token visible to the browser** (required for the Connect Worker modal's `docker run` command UX).
- **No UI-API authentication** when port 8765 is reached directly (relies on HA Ingress being the only path).
- **`secrets.yaml` delivered to every build worker** (required for ESPHome's `!secret` resolution).

If your deployment doesn't match the trusted-home-network model, read the audit carefully before exposing the add-on.
