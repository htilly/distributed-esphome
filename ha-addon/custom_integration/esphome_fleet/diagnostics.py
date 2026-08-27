"""QS.1 — Diagnostics support for the Fleet for ESPHome integration.

Exposes ``async_get_config_entry_diagnostics`` so HA's *Download
diagnostics* button (Settings → Devices & Services → Fleet for ESPHome →
⋮ → Download diagnostics) produces a JSON dump with enough detail to
reproduce a support issue, but with the sensitive bits redacted
(bearer token, direct-port URL with ``?token=…`` query params, API
encryption keys, WiFi creds leaked through ``device_attr`` if any).

Redaction uses ``async_redact_data`` from ``homeassistant.components.
diagnostics`` so the redaction shape is consistent with other HA
integrations.

Quality-scale note: this file is one of the Gold-tier requirements
(``diagnostics`` rule). Flipping ``quality_scale`` to ``gold`` in
``manifest.json`` is gated behind QS.9 landing every other rule.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, DOMAIN

# Keys whose values must never appear in a diagnostics dump. The set
# is intentionally broad — cheaper to over-redact than to leak a token
# through a field rename we forgot to update here.
_REDACT_CONFIG_ENTRY_DATA = {CONF_TOKEN}

# Coordinator snapshot redactions. The add-on's wire contract includes
# device-registry IDs, MAC addresses, and per-target API encryption
# keys in a few corners — scrub them so a diagnostics bundle shared on
# GitHub doesn't fingerprint the user's network.
_REDACT_COORDINATOR_DATA = {
    # Security review 2026-08-27 (finding O-006 / A-003): the coordinator
    # stores the raw JSON body of GET /ui/api/server-info under the
    # "server_info" key, and that body's first field — "token" — IS the
    # shared fleet-wide worker bearer credential (the same value that
    # authenticates every /api/v1/* worker request and is also accepted
    # as a system bearer on /ui/api/*). async_redact_data() walks nested
    # dicts recursively by key name, so this entry alone protects the
    # nested "server_info.token" field without needing a path-specific
    # rule. "server_token" is included defensively in case a future
    # field rename swaps the wire name without this set being updated.
    #
    # Attacker chain this closes: a user follows Home Assistant's
    # standard support workflow (Settings → Devices & Services → Fleet
    # for ESPHome → Download diagnostics) and attaches the result to a
    # public GitHub issue, as the project's own bug-report template
    # invites. Before this fix, that dump contained the token in
    # plaintext — anyone reading the issue could register as a worker
    # (claiming jobs and receiving every target's `!secret` values,
    # including WiFi PSKs and OTA passwords) and use the same token as
    # a system bearer on /ui/api/* to read, rewrite, or delete any
    # device config. No exploit chaining or special access was needed —
    # just reading a public GitHub issue.
    "token",
    "server_token",
    # Target-level fields
    "mac_address",
    "ha_device_id",
    # Worker-level fields
    "client_id",
    # System-info bag on each worker can contain hostname-level info
    # from uname/lsb_release — keep the shape but not the values.
    "system_info",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a redacted snapshot of the integration's state.

    Shape:

    ```
    {
        "config_entry": { ... entry.data with token redacted ... },
        "coordinator_data": { ... last /ui/api/* snapshot, redacted ... },
        "last_update_success": bool,
        "update_interval_seconds": int,
    }
    ```
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diag: dict[str, Any] = {
        "config_entry": async_redact_data(dict(entry.data), _REDACT_CONFIG_ENTRY_DATA),
    }

    if coordinator is not None:
        diag["coordinator_data"] = async_redact_data(
            coordinator.data or {}, _REDACT_COORDINATOR_DATA,
        )
        diag["last_update_success"] = coordinator.last_update_success
        diag["update_interval_seconds"] = (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval else None
        )
    else:
        # Setup failed or the entry is mid-unload — still useful to
        # surface that fact rather than return an empty dict.
        diag["coordinator_data"] = None
        diag["last_update_success"] = False
        diag["update_interval_seconds"] = None

    return diag
