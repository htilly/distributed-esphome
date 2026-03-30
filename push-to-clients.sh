#!/usr/bin/env bash
# Deploy the client package to all build client hosts.
# Builds fresh packages (arm64 + amd64) first, then distributes them.
#
# SERVER_URL and SERVER_TOKEN must be set in the environment, or the script
# reads them from the currently running local container.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$REPO_ROOT/ha-addon/VERSION")"
ARCHIVE_ARM64="$REPO_ROOT/dist/esphome-dist-client-${VERSION}-arm64.tar.gz"
ARCHIVE_AMD64="$REPO_ROOT/dist/esphome-dist-client-${VERSION}-x86_64.tar.gz"

# Try to read SERVER_URL/SERVER_TOKEN from the running container if not set
if [ -z "${SERVER_URL:-}" ] || [ -z "${SERVER_TOKEN:-}" ]; then
    if docker inspect esphome-dist-client >/dev/null 2>&1; then
        echo "==> Reading SERVER_URL/SERVER_TOKEN from running container ..."
        _env="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' esphome-dist-client)"
        SERVER_URL="${SERVER_URL:-$(echo "$_env" | grep '^SERVER_URL=' | cut -d= -f2-)}"
        SERVER_TOKEN="${SERVER_TOKEN:-$(echo "$_env" | grep '^SERVER_TOKEN=' | cut -d= -f2-)}"
    fi
fi

if [ -z "${SERVER_URL:-}" ] || [ -z "${SERVER_TOKEN:-}" ]; then
    echo "ERROR: SERVER_URL and SERVER_TOKEN must be set (or a local container must be running)." >&2
    exit 1
fi

export SERVER_URL SERVER_TOKEN

# Build packages if they don't exist
if [ ! -f "$ARCHIVE_ARM64" ]; then
    echo "==> Building arm64 package ..."
    "$REPO_ROOT/package-client.sh" "$SERVER_URL" "$SERVER_TOKEN" linux/arm64
fi
if [ ! -f "$ARCHIVE_AMD64" ]; then
    echo "==> Building amd64 package ..."
    "$REPO_ROOT/package-client.sh" "$SERVER_URL" "$SERVER_TOKEN" linux/amd64
fi

echo "==> Distributing v${VERSION} to clients ..."
echo "    SERVER_URL=$SERVER_URL"

# --- Local: this machine (arm64) ---
LOCAL_DIR="/Users/stefan/tmp/de-client"
echo ""
echo "--- localhost → $LOCAL_DIR ---"
mkdir -p "$LOCAL_DIR"
tar -xzf "$ARCHIVE_ARM64" -C "$LOCAL_DIR"
cd "$LOCAL_DIR"
./stop.sh 2>/dev/null || true
./start.sh --background
cd "$REPO_ROOT"

# --- Remote hosts (arm64) ---
deploy_remote() {
    local host="$1"
    local dest_dir="$2"
    local archive="$3"

    echo ""
    echo "--- $host → $dest_dir ---"
    scp "$archive" "${host}:/tmp/esphome-dist-client.tar.gz"
    # Pass SERVER_URL and SERVER_TOKEN via ssh so start.sh can use them
    ssh "$host" "
        set -e
        export PATH=\"/usr/local/bin:/opt/homebrew/bin:\$PATH\"
        export SERVER_URL='$SERVER_URL'
        export SERVER_TOKEN='$SERVER_TOKEN'
        mkdir -p '$dest_dir'
        tar -xzf /tmp/esphome-dist-client.tar.gz -C '$dest_dir'
        rm /tmp/esphome-dist-client.tar.gz
        cd '$dest_dir'
        ./stop.sh 2>/dev/null || true
        ./start.sh --background
    "
}

deploy_remote "ai-mac"    "/Users/dan/de-client"    "$ARCHIVE_ARM64"
deploy_remote "macdaddy"  "/Users/stefan/de-client"  "$ARCHIVE_ARM64"

echo ""
echo "==> v${VERSION} deployed to all clients."
echo ""
echo "Windows (amd64) package ready at:"
echo "  $ARCHIVE_AMD64"
echo "  Copy to Windows host, extract, then run:"
echo "  \$env:SERVER_URL='$SERVER_URL'; \$env:SERVER_TOKEN='...'; .\\start.ps1"
