#!/usr/bin/env bash
#
# onboard.sh
# Complete Home Assistant's onboarding flow automatically on a freshly
# installed HAOS VM (e.g. the one created by scripts/haos/provision-vm.sh).
#
# What this automates:
#   - Waits for HA's REST API to come up.
#   - Creates the first admin user via /api/onboarding/users.
#   - Exchanges the returned auth_code for a short-lived access token via
#     /auth/token, then uses it to finish the remaining onboarding steps
#     (core_config, analytics, integration).
#   - Mints a long-lived access token and saves it to disk so push-to-haos.sh
#     can hit the HA REST API non-interactively.
#
# Does NOT cover:
#   - Installing the add-on or integration (scripts/haos/install-addon.sh).
#   - The qemu-guest-agent — HAOS 10+ ships it built in and running, so
#     provision-vm.sh sets `-agent 1` and Proxmox picks it up on next start.
#
# Usage:
#   HA_PASSWORD="secret123" scripts/haos/onboard.sh http://192.168.224.17:8123
#
# Env overrides (all optional):
#   HA_USERNAME=admin HA_NAME=Admin HA_LANGUAGE=en
#   HA_TIME_ZONE=UTC HA_COUNTRY=US HA_CURRENCY=USD HA_UNIT_SYSTEM=metric
#   HA_LOCATION_NAME=Home HA_LATITUDE=0 HA_LONGITUDE=0 HA_ELEVATION=0
#   TOKEN_FILE=$HOME/.config/distributed-esphome/haos-token
#   HAOS_AUTHORIZED_KEYS_FILE=$HOME/.config/distributed-esphome/haos-authorized-keys
#       Public keys to authorize on the Advanced SSH add-on. Defaults to the
#       file above if it exists, otherwise to ~/.ssh/id_ed25519.pub. Set to
#       the empty string to skip the SSH add-on install entirely.
#   PVE_HOST=pve  VMID=106
#       The Advanced SSH add-on is installed via qemu-guest-agent against
#       the VM (HA's REST Supervisor proxy rejects user tokens). These default
#       to the same values provision-vm.sh uses.

set -euo pipefail

HA_URL="${1:-${HA_URL:-}}"
[[ -n "$HA_URL" ]] || { echo "Usage: $0 <ha_url>  (or set HA_URL env)" >&2; exit 1; }
HA_URL="${HA_URL%/}"   # strip trailing slash

HA_PASSWORD="${HA_PASSWORD:?set HA_PASSWORD env}"
HA_USERNAME="${HA_USERNAME:-admin}"
HA_NAME="${HA_NAME:-Admin}"
HA_LANGUAGE="${HA_LANGUAGE:-en}"
HA_TIME_ZONE="${HA_TIME_ZONE:-UTC}"
HA_COUNTRY="${HA_COUNTRY:-US}"
HA_CURRENCY="${HA_CURRENCY:-USD}"
HA_UNIT_SYSTEM="${HA_UNIT_SYSTEM:-metric}"
HA_LOCATION_NAME="${HA_LOCATION_NAME:-Home}"
HA_LATITUDE="${HA_LATITUDE:-0}"
HA_LONGITUDE="${HA_LONGITUDE:-0}"
HA_ELEVATION="${HA_ELEVATION:-0}"

CLIENT_ID="${HA_URL}/"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/distributed-esphome/haos-token}"
LL_TOKEN_NAME="${LL_TOKEN_NAME:-distributed-esphome-onboarding}"

# Advanced SSH & Web Terminal add-on (Frenck, slug a0d7b954_ssh) — installed
# after onboarding so we can SSH into the throwaway HAOS VM with the same
# id_ed25519 the rest of the home lab uses. The repo at
# github.com/hassio-addons/repository isn't pre-installed on stock HAOS,
# so we add it before installing the add-on itself.
SSH_ADDON_SLUG="a0d7b954_ssh"
SSH_ADDON_REPO="https://github.com/hassio-addons/repository"
DEFAULT_SSH_KEYS_FILE="$HOME/.config/distributed-esphome/haos-authorized-keys"
if [[ -z "${HAOS_AUTHORIZED_KEYS_FILE+x}" ]]; then
  if [[ -f "$DEFAULT_SSH_KEYS_FILE" ]]; then
    HAOS_AUTHORIZED_KEYS_FILE="$DEFAULT_SSH_KEYS_FILE"
  else
    HAOS_AUTHORIZED_KEYS_FILE="$HOME/.ssh/id_ed25519.pub"
  fi
fi
PVE_HOST="${PVE_HOST:-pve}"
VMID="${VMID:-106}"

command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
# The long-lived-token step below embeds a Python script that imports aiohttp.
# Check up front so we fail fast with a clear install hint instead of a
# ModuleNotFoundError 30 seconds into the onboarding flow.
python3 -c 'import aiohttp' 2>/dev/null \
  || { echo "python3 aiohttp package is required (pip install aiohttp)" >&2; exit 2; }

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# --- Wait for HA to accept requests ----------------------------------------

log "Waiting for $HA_URL/api/onboarding to respond..."
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 "$HA_URL/api/onboarding" >/dev/null; then
    break
  fi
  sleep 5
done
curl -fsS --max-time 5 "$HA_URL/api/onboarding" >/dev/null \
  || { echo "HA did not respond at $HA_URL" >&2; exit 3; }

STATUS=$(curl -fsS "$HA_URL/api/onboarding")
log "Onboarding status: $STATUS"

step_done() {
  jq -e --arg s "$1" '.[] | select(.step == $s) | .done' <<<"$STATUS" | grep -qx true
}

# --- Step 1: user ----------------------------------------------------------

if step_done user; then
  log "Step 'user' already done — skipping user creation"
  echo "Already onboarded; won't create a duplicate user. If you need a" >&2
  echo "new long-lived token, generate one in the UI under your profile." >&2
  exit 0
fi

log "Creating admin user '$HA_USERNAME'"
USER_PAYLOAD=$(jq -n \
  --arg client_id "$CLIENT_ID" \
  --arg name "$HA_NAME" \
  --arg username "$HA_USERNAME" \
  --arg password "$HA_PASSWORD" \
  --arg language "$HA_LANGUAGE" \
  '{client_id: $client_id, name: $name, username: $username, password: $password, language: $language}')

USER_RESP=$(curl -fsS -X POST "$HA_URL/api/onboarding/users" \
  -H "Content-Type: application/json" \
  -H "Origin: $HA_URL" \
  --data "$USER_PAYLOAD")

AUTH_CODE=$(jq -r '.auth_code' <<<"$USER_RESP")
[[ -n "$AUTH_CODE" && "$AUTH_CODE" != "null" ]] \
  || { echo "No auth_code in user-creation response: $USER_RESP" >&2; exit 4; }

log "Exchanging auth_code for short-lived access token"
TOKEN_RESP=$(curl -fsS -X POST "$HA_URL/auth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=$AUTH_CODE")

ACCESS_TOKEN=$(jq -r '.access_token' <<<"$TOKEN_RESP")
[[ -n "$ACCESS_TOKEN" && "$ACCESS_TOKEN" != "null" ]] \
  || { echo "No access_token: $TOKEN_RESP" >&2; exit 5; }

auth_header=(-H "Authorization: Bearer $ACCESS_TOKEN")

# --- Step 2: core_config ---------------------------------------------------

if ! step_done core_config; then
  log "Completing core_config"
  CORE_PAYLOAD=$(jq -n \
    --arg time_zone "$HA_TIME_ZONE" \
    --arg country "$HA_COUNTRY" \
    --arg currency "$HA_CURRENCY" \
    --arg unit_system "$HA_UNIT_SYSTEM" \
    --arg language "$HA_LANGUAGE" \
    --arg location_name "$HA_LOCATION_NAME" \
    --argjson latitude "$HA_LATITUDE" \
    --argjson longitude "$HA_LONGITUDE" \
    --argjson elevation "$HA_ELEVATION" \
    '{time_zone: $time_zone, country: $country, currency: $currency,
      unit_system: $unit_system, language: $language,
      location_name: $location_name,
      latitude: $latitude, longitude: $longitude, elevation: $elevation}')
  curl -fsS -X POST "$HA_URL/api/onboarding/core_config" \
    "${auth_header[@]}" -H "Content-Type: application/json" \
    --data "$CORE_PAYLOAD" >/dev/null
fi

# --- Step 3: analytics (opt out) -------------------------------------------

if ! step_done analytics; then
  log "Completing analytics (opting out)"
  curl -fsS -X POST "$HA_URL/api/onboarding/analytics" \
    "${auth_header[@]}" -H "Content-Type: application/json" \
    --data '{}' >/dev/null
fi

# --- Step 4: integration ---------------------------------------------------

if ! step_done integration; then
  log "Completing integration step"
  INT_PAYLOAD=$(jq -n \
    --arg client_id "$CLIENT_ID" \
    --arg redirect_uri "${HA_URL}/?auth_callback=1" \
    '{client_id: $client_id, redirect_uri: $redirect_uri}')
  curl -fsS -X POST "$HA_URL/api/onboarding/integration" \
    "${auth_header[@]}" -H "Content-Type: application/json" \
    --data "$INT_PAYLOAD" >/dev/null
fi

# --- Mint a long-lived access token ----------------------------------------

log "Minting long-lived access token '$LL_TOKEN_NAME'"
# The long-lived token endpoint is WebSocket-only; use aiohttp.
LL_TOKEN=$(python3 - "$HA_URL" "$ACCESS_TOKEN" "$LL_TOKEN_NAME" <<'PY'
import asyncio, json, sys
import aiohttp

url, access_token, name = sys.argv[1:4]
ws_url = url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            # 1st frame: auth_required
            await ws.receive_json(timeout=10)
            await ws.send_json({"type": "auth", "access_token": access_token})
            auth_resp = await ws.receive_json(timeout=10)
            if auth_resp.get("type") != "auth_ok":
                print(f"auth failed: {auth_resp}", file=sys.stderr); sys.exit(6)
            await ws.send_json({
                "id": 1,
                "type": "auth/long_lived_access_token",
                "client_name": name,
                "lifespan": 3650,
            })
            resp = await ws.receive_json(timeout=10)
            if not resp.get("success"):
                print(f"LL token request failed: {resp}", file=sys.stderr); sys.exit(7)
            print(resp["result"])

asyncio.run(main())
PY
)

mkdir -p "$(dirname "$TOKEN_FILE")"
umask 077
printf '%s\n' "$LL_TOKEN" > "$TOKEN_FILE"
log "Long-lived token saved to $TOKEN_FILE"

# --- Advanced SSH add-on ---------------------------------------------------
# Install via qemu-guest-agent: HA's `/api/hassio/*` Supervisor proxy
# rejects long-lived user tokens with 401, so we drive `ha` from inside
# the guest the same way scripts/haos/install-addon.sh does. Each step is
# idempotent (re-adding a repo, re-installing, or restarting are all safe).

if [[ -z "$HAOS_AUTHORIZED_KEYS_FILE" ]]; then
  log "HAOS_AUTHORIZED_KEYS_FILE empty — skipping Advanced SSH add-on install"
elif [[ ! -f "$HAOS_AUTHORIZED_KEYS_FILE" ]]; then
  log "WARNING: $HAOS_AUTHORIZED_KEYS_FILE not found — skipping Advanced SSH add-on install"
else
  AUTHORIZED_KEYS_JSON=$(jq -R -s 'split("\n") | map(select(length > 0 and (startswith("#") | not)))' \
    < "$HAOS_AUTHORIZED_KEYS_FILE")
  KEY_COUNT=$(jq 'length' <<<"$AUTHORIZED_KEYS_JSON")
  if [[ "$KEY_COUNT" == "0" ]]; then
    log "WARNING: $HAOS_AUTHORIZED_KEYS_FILE has no usable keys — skipping Advanced SSH add-on install"
  else
    log "Installing Advanced SSH & Web Terminal add-on ($KEY_COUNT key(s) from $HAOS_AUTHORIZED_KEYS_FILE)"

    # Auto-detect PVE node name (matches install-addon.sh's pattern).
    PVE_NODE=$(ssh "$PVE_HOST" "pvesh get /nodes --output-format json" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['node'])" 2>/dev/null) \
      || { echo "Couldn't auto-detect PVE_NODE on $PVE_HOST" >&2; exit 8; }

    # Run a shell command inside the HAOS guest via qga; prints stdout/stderr,
    # returns the guest's exit code. SCRIPT_FILE is uploaded to PVE first to
    # avoid quoting hell when the script contains JSON.
    guest_exec() {
      local script="$1" timeout_s="${2:-600}"
      local remote_script
      remote_script=$(ssh "$PVE_HOST" mktemp -t guest_exec.XXXXXX)
      printf '%s' "$script" | ssh "$PVE_HOST" "cat > $remote_script"
      ssh "$PVE_HOST" "PVE_NODE=$PVE_NODE VMID=$VMID TIMEOUT_S=$timeout_s SCRIPT_FILE=$remote_script bash -s" <<'REMOTE'
set -euo pipefail
TMPJSON=$(mktemp)
trap 'rm -f "$TMPJSON" "$SCRIPT_FILE"' EXIT
SCRIPT=$(cat "$SCRIPT_FILE")
pvesh create "/nodes/$PVE_NODE/qemu/$VMID/agent/exec" \
  --command /bin/sh --command -c --command "$SCRIPT" \
  --output-format json > "$TMPJSON"
PID=$(python3 -c "import sys,json; print(json.load(open(sys.argv[1]))['pid'])" "$TMPJSON")
for _ in $(seq 1 "$TIMEOUT_S"); do
  pvesh get "/nodes/$PVE_NODE/qemu/$VMID/agent/exec-status" \
    --pid "$PID" --output-format json > "$TMPJSON"
  if python3 -c "import sys,json; sys.exit(0 if json.load(open(sys.argv[1])).get('exited') else 1)" "$TMPJSON"; then
    python3 - "$TMPJSON" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
if d.get("out-data"): sys.stdout.write(d["out-data"])
if d.get("err-data"): sys.stderr.write(d["err-data"])
sys.exit(d.get("exitcode", 0))
PY
    exit $?
  fi
  sleep 1
done
echo "guest exec timed out after ${TIMEOUT_S}s" >&2
exit 124
REMOTE
    }

    # 1. Add the community add-ons repo + reload (no-op if already present).
    log "Adding $SSH_ADDON_REPO to the add-on store"
    guest_exec "docker exec hassio_cli ha store add '$SSH_ADDON_REPO' --no-progress 2>&1 || true
docker exec hassio_cli ha store reload --no-progress 2>&1 || true" 120 >/dev/null

    # 2. Install the add-on (no-op if already installed).
    log "Installing $SSH_ADDON_SLUG (first install pulls ~50MB; up to ~3 min)"
    guest_exec "docker exec hassio_cli ha apps install $SSH_ADDON_SLUG --no-progress 2>&1 || true" 600 >/dev/null

    # 3. Set options. The `ha` CLI has no first-class options command, so we
    # POST to the Supervisor REST API from inside the hassio_cli container,
    # which already has SUPERVISOR_TOKEN in its env. Schema nests SSH config
    # under .ssh per the a0d7b954_ssh add-on.
    OPTS_JSON=$(jq -c -n --argjson keys "$AUTHORIZED_KEYS_JSON" '{
      options: {
        ssh: {
          username: "root",
          password: "",
          authorized_keys: $keys,
          sftp: true,
          compatibility_mode: false,
          allow_agent_forwarding: false,
          allow_remote_port_forwarding: false,
          allow_tcp_forwarding: false
        },
        zsh: true,
        share_sessions: true,
        packages: [],
        init_commands: []
      },
      network: {"22/tcp": 22}
    }')
    OPTS_B64=$(printf '%s' "$OPTS_JSON" | base64 | tr -d '\n')

    log "Configuring $SSH_ADDON_SLUG (authorized_keys + defaults)"
    guest_exec "set -e
echo '$OPTS_B64' | base64 -d > /tmp/ssh-addon-options.json
docker cp /tmp/ssh-addon-options.json hassio_cli:/tmp/ssh-addon-options.json
docker exec hassio_cli sh -c 'curl -fsS -X POST http://supervisor/addons/$SSH_ADDON_SLUG/options \
  -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" \
  -H \"Content-Type: application/json\" \
  --data @/tmp/ssh-addon-options.json'
rm -f /tmp/ssh-addon-options.json
docker exec hassio_cli ha apps restart $SSH_ADDON_SLUG --no-progress 2>&1 || \
  docker exec hassio_cli ha apps start $SSH_ADDON_SLUG --no-progress 2>&1
" 120 >/dev/null

    _haos_host=$(echo "$HA_URL" | sed -E 's#^https?://##; s#:[0-9]+$##; s#/.*$##')
    log "Advanced SSH add-on running at ssh root@${_haos_host}  (port 22)"
  fi
fi

cat >&2 <<EOF

Onboarding complete.
  HA:          $HA_URL
  User:        $HA_USERNAME
  Token file:  $TOKEN_FILE  (3650-day lifespan)

Verify:
  curl -sH "Authorization: Bearer \$(cat $TOKEN_FILE)" $HA_URL/api/ | jq .

SSH into the VM (Advanced SSH add-on, port 22 — drops you in the
  add-on container with \`ha\` and \`docker\` available):
  ssh haos-pve

Next:
  HAOS_URL=$HA_URL push-to-haos.sh

EOF
