# Server-Side OTA for Thread/Matter Devices — Design Note

**Problem:** Thread devices communicate over an IPv6 mesh network (the `openthread:` YAML block) that's only reachable from the Home Assistant host itself. A remote build worker — whether it's a machine on the local LAN, or a container in AWS/Azure/any cloud — has no route into that mesh. The normal flow (worker compiles *and* flashes) silently fails for these devices: the worker can compile firmware fine, but the OTA upload times out or errors because the device is simply unreachable from wherever the worker happens to sit.

**Constraint that shapes the design:** only the HA host has network access to the Thread mesh. This is a physical/topological fact, not a permissions or config issue — no amount of retrying or reconfiguring the worker fixes it. Any solution has to guarantee the OTA *push* happens from the HA host specifically, regardless of which worker did the compile.

**Solution — split compile/flash:**
1. **Any worker compiles.** Worker selection is unaffected — the job scheduler doesn't need to know or care which workers "can reach" a Thread device, because none of them ever try. This means Thread jobs still benefit from distributed build capacity (a fast cloud worker can compile a Thread device's firmware just as well as a WiFi device's).
2. **The worker uploads the binary, not the device.** It reuses the existing `download_only` job path unchanged — compile, archive the `.bin` to the server, stop. No new capability needed on the worker side.
3. **The server performs the actual flash.** Running on the HA host, it has direct route to the Thread mesh. It extracts a fresh config bundle, writes the binary next to the target YAML, and runs `esphome upload --device <addr> --file <bin> <target.yaml>` itself.

**Auto-detection, not configuration.** Whether a job gets this treatment is decided by the *device's own YAML* (presence of an `openthread:` block), detected once at enqueue time — not by a per-worker setting, a "this worker can't reach Thread" flag, or anything the operator has to configure. A worker never has to determine its own reachability; the server tells it up front (`server_ota=True` on the job) that this one is compile-only, and the worker just obeys that flag the same way it already obeys `download_only`.

**Why this reuses `download_only` rather than adding a new job type:** the mechanics are identical (compile, archive binary, stop) — the only thing that differs is *who* performs the subsequent OTA and from *where*. Piggybacking on the existing path means zero new worker-side machinery; the only new code is the flag threading through the queue and the small `_server_ota_push` step that runs after a compile-only success.

**Where this lives in the code**, for reviewers:
- `ha-addon/server/scanner.py` / `ui_api.py` / `scheduler.py` — Thread detection from the YAML (`network_type == "thread"`), at both full-resolution and raw-YAML-fallback paths.
- `ha-addon/server/job_queue.py`, `protocol.py` (both server/client copies) — the `server_ota` field, additive and optional so older workers degrade gracefully (they'd just never see it set).
- `ha-addon/client/client.py` — `server_ota` maps to `download_only=True`; no other worker change.
- `ha-addon/server/api.py` — `_server_ota_push`, the actual `esphome upload` invocation from the server after a compile-only success.
- `ARCHITECTURE.md` — the permanent reference for this split, including the two-path diagram (Thread via server / WiFi-Ethernet via worker).
