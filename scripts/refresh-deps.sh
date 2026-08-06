#!/usr/bin/env bash
# Regenerate the hash-pinned Python dependency lockfiles (E.1).
#
# We keep ``ha-addon/{server,client}/requirements.txt`` as the human-edited
# input — direct dependencies with ``>=`` ranges. ``pip-compile --generate-hashes``
# resolves these to a fully-pinned, hash-locked ``requirements.lock`` that
# the Dockerfiles install with ``--require-hashes``.
#
# Run locally:
#   bash scripts/refresh-deps.sh
#
# For a targeted CVE/security bump without re-resolving every other pin,
# pass one or more --upgrade-package flags through to pip-compile, e.g.:
#   bash scripts/refresh-deps.sh --upgrade-package aiohttp --upgrade-package cryptography
# Without any flags, pip-compile keeps existing pins that still satisfy
# requirements.txt's >= ranges rather than proactively bumping them — so
# a plain re-run after a CVE disclosure is usually a no-op; you need the
# targeted --upgrade-package to actually move just the flagged package(s).
#
# Do NOT pass a blanket --upgrade — that's what caused the 1.4.1-dev.55
# incident (pulled in pyobjc-core as an unmarked macOS-only transitive,
# broke the linux/amd64 build). --upgrade-package only lets the named
# package(s) move; everything else stays pinned as-is. This script
# refuses a bare --upgrade for that reason.
#
# Should be run + committed before every release (the RELEASE_CHECKLIST has
# a step for this) and any time direct deps in requirements.txt change.

set -euo pipefail

for arg in "$@"; do
    if [[ "$arg" == "--upgrade" ]]; then
        echo "Refusing a blanket --upgrade — see this script's header (1.4.1-dev.55 incident)." >&2
        echo "Use --upgrade-package <name> instead, once per package you want to move." >&2
        exit 1
    fi
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# CRITICAL: lockfiles must be generated on the same platform the Dockerfiles
# install on (linux/amd64), otherwise platform-conditional transitive deps
# leak in. The 1.3.1-dev.9 deploy failure was caused by generating the lock
# on macOS, which pulled in PyObjC as a transitive — Linux can't install
# that. We pin to python:3.13-slim because that's what both Dockerfiles
# FROM. Re-run via Docker so the result is reproducible regardless of host.

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found — required to generate lockfiles on the target platform."
    exit 1
fi

echo "▶ Refreshing lockfiles inside python:3.13-slim (linux/amd64)…"

docker run --rm \
    --platform linux/amd64 \
    -v "$REPO_ROOT":/work \
    -w /work \
    python:3.13-slim \
    bash -c '
        set -e
        apt-get update -qq && apt-get install -qq -y --no-install-recommends gcc libffi-dev libssl-dev git >/dev/null
        pip install --quiet pip-tools
        echo "  ▶ ha-addon/server/requirements.lock"
        # "$@" here is whatever --upgrade-package flags (if any) were
        # passed to this script — see the header for why a blanket
        # --upgrade is refused outright (#51 / 1.4.1-dev.55 incident).
        pip-compile \
            --generate-hashes \
            --resolver=backtracking \
            --strip-extras \
            --quiet \
            "$@" \
            --output-file ha-addon/server/requirements.lock \
            ha-addon/server/requirements.txt
        echo "  ▶ ha-addon/client/requirements.lock"
        pip-compile \
            --generate-hashes \
            --resolver=backtracking \
            --strip-extras \
            --quiet \
            "$@" \
            --output-file ha-addon/client/requirements.lock \
            ha-addon/client/requirements.txt
    ' bash "$@"

echo ""
echo "✅ Lockfiles regenerated. Review the diff and commit:"
echo "   git diff ha-addon/server/requirements.lock ha-addon/client/requirements.lock"
