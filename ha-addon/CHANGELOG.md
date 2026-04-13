# Changelog

## 1.4.0

The fleet management release. New per-device scheduling, "+ New Device" creation, a Schedules tab, and a top-to-bottom UX consistency pass across all four tabs.

**New features**

- **Per-device scheduled upgrades** — every device can now have a recurring cron schedule (Daily 2am, Weekly Sunday, custom cron) or a one-time scheduled upgrade. The schedule is stored in the YAML file as a `# distributed-esphome:` comment block — it travels with the file, no separate database. A new **Schedules tab** lists every scheduled device with its next/last run, status (Active / Paused / One-time), version pin, and history of recent fires (✓ success / ✗ failed / ● pending). Set schedules from the per-device hamburger menu or the bulk Actions dropdown. Schedule columns and last-run timestamps are clickable to open the upgrade dialog directly.
- **Unified Upgrade modal** — the per-device Upgrade dialog and the Schedule dialog are now one modal with a Now / Scheduled mode toggle. Worker selection, ESPHome version, and (when scheduled) the cron picker all live in one place. The version dropdown is searchable with a "Show betas" toggle, and includes ALL historic ESPHome versions from PyPI (not just the most recent 50). The dialog title changes to "Schedule Upgrade — X" in scheduled mode.
- **"+ New Device" and "Duplicate…"** — create a new device YAML from a stub or duplicate an existing one. Both flows open the editor on the new file. The file is staged as a hidden dotfile until first save, so cancelling out doesn't leave orphan entries in the Devices tab. Duplicate preserves `!include` packages, `!secret` references, and rewrites `substitutions.name` so the renamed device picks up its new name everywhere.
- **Per-device version pinning** — pin any device to a specific ESPHome version (📌 icon next to the version). Bulk upgrades respect the pin. Pinned devices validate against their pinned version (the server installs the pinned ESPHome on demand if it differs from the global default). The upgrade modal includes a "Current" option that always uses whatever's installed.
- **Home Assistant device deep-links** — the "Yes" indicator in the HA column is now a clickable link that opens the device's page in Home Assistant. Works for both managed devices (with YAML) and unmanaged ones HA knows about.
- **Schedules tab** with TanStack Table, search, sticky headers, sortable columns, and bulk Remove via the Actions dropdown. Same layout patterns as the other three tabs.
- **Cancelled job state** — cancelling a queued or running job now marks it as `Cancelled` (gray badge) instead of `Failed`. Cancelled jobs can be retried like any other terminal state.
- **Clear Entire Queue** — new option in the Queue tab's Clear dropdown that cancels all running/pending jobs and clears all terminal jobs in one action.
- **Sticky action column** — the rightmost column with action buttons (Upgrade / Edit / ⋮) is now pinned to the right edge of the table when it scrolls horizontally on narrow viewports — buttons stay accessible at any window size.

**Improvements**

- **Tab consistency** — all four tabs (Devices / Queue / Workers / Schedules) now share the same toolbar layout: title + search + dropdowns. Same TanStack Table, same SortHeader, same checkbox selection, same row styling, same empty-state pattern. Bulk actions live in an Actions dropdown — always visible, items disabled when nothing's selected (no more buttons that pop in and out).
- **Standardized toolbar layout** — primary "Add new" actions (`+ New Device`, `+ Connect Worker`) are always FIRST as primary buttons; tab-specific operations (Upgrade / Retry / Clear) sit in the middle; bulk actions are LAST in the Actions dropdown.
- **Tighter table layout** — reduced horizontal/vertical padding and harmonized font sizes for better screen-estate use on fleet management dashboards. Checkbox column hugs the device name. The local-worker row highlight extends across all cells including the sticky action column.
- **Editor and Logs windows scale with the viewport** — both modals now use most of the available screen space (viewport minus 3rem margin) using dynamic viewport units that adapt to embedded contexts like HA Ingress.
- **Schedule history view** — the Schedules tab's "Last Run" column shows the most recent fire event with a status indicator. Hovering reveals the last 5 runs. History persists across server restarts.
- **Per-slot compile working dirs + shared cache** — concurrent compiles on the same worker no longer race on PlatformIO's `.pio/` and `.esphome/` directories. Each worker slot has its own working dir, with a shared per-target cache that's seeded on slot startup and promoted on successful compile. Compiles stay fully parallel and cache reuses across slots.
- **Local-time scheduling** — schedule pickers display and accept local time; cron expressions are stored in UTC so schedules fire at the wall-clock time you set, regardless of timezone.
- **Faster scheduler** — replaced the in-house scheduler with APScheduler. Schedules fire within milliseconds of their target time (was up to 60s polling delay), and changes take effect immediately when you save them. Misfire grace window prevents stale post-restart fires; coalesce + max-instances=1 prevents double-fire.
- **Sticky workspace state** — the active tab persists across page reloads; the local worker's slot count survives add-on restarts.
- **HA backup size reduction** — excluded the worker's `/data/esphome-versions/` cache from HA backups (was bloating backups by 1-2 GB after a fleet upgrade).

**Bug fixes**

- Schedules now fire at the correct time. Fixed a timezone bug where cron expressions stored the user's local hour directly, causing schedules to fire 7+ hours off in non-UTC timezones.
- Schedules created on the very first run now wait for the next cron occurrence instead of firing immediately.
- One-time schedules can be set for "now" without rejection.
- The Remove Schedule action now removes both recurring and one-time schedules (previously only touched recurring).
- Web server links restored — devices using `web_server:` (with no value, i.e. defaults) now correctly show their IP as a clickable link.
- The MAC-based HA matcher now finds all devices — split a too-large supervisor template query that was returning empty.
- "+ New Device" and "Duplicate…" no longer return 404 — fixed the staging path that broke URL routing.
- Cloned devices no longer appear "online" or "in HA" before the user saves them — the file is staged invisibly until first save.
- Editor cleanup on cancel — closing the editor on a freshly created device without saving now deletes the staged file instead of leaving an orphan.
- Cancelled jobs can be retried (was incorrectly excluded from the retry-eligible set).
- Retry honors per-device pinned version (was always using the server default).
- Ghost device rows after deleting an unflashed clone are now cleaned up; configured devices that go offline for >4 hours are auto-purged.
- Button heights and border-radii are now uniform across all toolbar buttons and dropdowns.
- Dialog modals no longer hang outside the viewport on narrow screens.
- Status indicators, fonts, and pin icons render consistently across all four tabs.
- Validation now uses the device's pinned ESPHome version (installs it on demand) instead of failing with the server's default version.

**Under the hood**

- Replaced the DIY scheduler with APScheduler for reliable execution (~ms latency, immediate sync on schedule changes, correct misfire handling).
- Eliminated all aiohttp `DeprecationWarning`s by routing dynamic state through a single mutable container.
- Removed Supervisor `/addons` listing call that returned 403 every 30s and spammed the host log.
- Persisted scheduler history to `/data/schedule_history.json` (survives restarts).
- New `pip-audit` regression guard (PY-8): every direct dep in `requirements.txt` must be in `requirements.lock`.
- Post-deploy add-on log check (`scripts/check-addon-logs.sh`) wired into the dev loop.
- 45 mocked Playwright e2e tests + 6 hass-4 prod smoke tests; full suite passes.

## 1.3.1

**New features**

- **Upgrade modal** — clicking Upgrade on a device opens a dialog where you can pick which worker should run the build and which ESPHome version to use. The version override is per-job only — it won't change your global default. Replaces the old "Upgrade on..." submenu.
- **Queued follow-up compiles** — clicking Upgrade while a compile is already running for the same device queues exactly one follow-up that starts automatically when the current build finishes. It picks up the latest YAML at the time it starts, so you can edit → save → click Upgrade again without waiting. Re-clicking a third time updates the queued follow-up (worker, version) instead of piling up entries. The Queue tab shows a "Queued" badge on these follow-up jobs.
- **Network columns on the Devices tab** — new toggleable columns show each device's network type (WiFi / Ethernet / Thread), IP mode (Static / DHCP), IPv6 status, Matter support, and whether a fallback access point is configured. The "Net" column is visible by default; the others can be toggled from the column picker.
- **Upgrading indicator** ([#32](https://github.com/weirded/distributed-esphome/issues/32)) — an orange pulsing dot appears in the Status column while a device has a compile in flight, with live status text ("Compiling…", "OTA Retry", etc.) from the queue. No more wondering whether your Upgrade click registered.
- **Save & Upgrade goes through the modal** — the editor's "Save & Upgrade" button now opens the same Upgrade dialog so you can pick a worker and version before triggering the build.
- **Queue tab improvements** — new "Version" column shows the ESPHome version each job will compile against. A 📌 pushpin icon appears next to workers that were explicitly chosen in the Upgrade modal. Successful jobs show a green "Rerun" button instead of the amber "Retry".
- **HA-confirmed unmanaged devices** — devices discovered via mDNS that don't have a YAML config but ARE known to Home Assistant now show "in HA" under the IP and "Yes" in the HA column, so you can tell real ESPHome devices from stray mDNS broadcasts.
- **Connect Worker modal remembers context** — clicking the "Image Stale" badge on a worker pre-populates the hostname, max parallel jobs, and host platform from the existing worker.

**Improvements**

- Devices, Workers, and Queue tabs now poll at 1 Hz (was 3–15 seconds) for much snappier updates.
- After a successful OTA, the device's running version updates within ~1 second instead of waiting up to 60 seconds for the next poll cycle.
- Compile and clean-cache actions instantly refresh the relevant UI data instead of lagging by one poll interval.
- Unavailable actions (like Restart on devices without a restart button in their YAML) are now grayed out with an explanatory tooltip instead of silently failing when clicked.
- ESPHome add-on version detection works with any slug format (including hashed community-repo slugs) without needing elevated Supervisor permissions.
- Repeated identical warnings from the HA entity poller are demoted to DEBUG after the second occurrence, so a persistent HA outage doesn't drown the logs.
- Every 401 rejection now logs a structured reason (missing header, wrong scheme, token mismatch) with the peer IP, making auth issues much easier to diagnose.

**Bug fixes**

- [#25](https://github.com/weirded/distributed-esphome/issues/25) — UI didn't load on HAOS with 1.3.0 (startup blocked on Supervisor API + poller tight-retry loop).
- [#27](https://github.com/weirded/distributed-esphome/issues/27) — Divider line between managed and unmanaged devices disappeared on toggle.
- [#31](https://github.com/weirded/distributed-esphome/issues/31) — "Upgrade on..." submenu overflowed with long hostnames and closed when moving the mouse to it.
- [#6](https://github.com/weirded/distributed-esphome/issues/6) — Intermittent `Failed to install Python dependencies into penv` on ARM Mac workers (increased network timeouts for uv/pip).
- Restart endpoint no longer silently reports success when the device has no restart button — it returns a clear error with the candidates it tried.
- A corrupted `queue.json` entry no longer crashes the server at startup — the bad entry is skipped and logged.
- Matter/Thread devices with both `wifi:` and `openthread:` blocks are now correctly detected as Thread (was incorrectly picking WiFi due to block-order precedence).
- Editor no longer gets stuck on a loading screen (CSP was blocking Monaco's CDN; now allowed).

**Under the hood**

- Server↔worker payloads are now typed via pydantic v2 models with protocol versioning and forward-compatible field handling. Malformed requests return structured errors instead of being half-processed.
- Python dependencies are hash-pinned in lockfiles and installed with `--require-hashes`. pip-audit + npm audit gate CI. Dependabot configured for all ecosystems. GHCR images are cosign-signed.
- Security response headers (CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, X-Frame-Options) on every UI response.
- 338 Python tests (was 264), 37 mocked Playwright tests, 6 prod Playwright tests against a real HA instance with real device compilation + OTA.
- New `scripts/check-invariants.sh` enforces 8 codebase rules in CI (no `fetch()` outside `api/`, no `any` in TS, YAML via `safe_load` only, etc.).

## 1.3.0

Theme: **Quality + Testing.** Mostly internal hardening to prevent regressions and increase confidence in future releases. A handful of user-visible bug fixes and small UX improvements ride along.

**Reliability infrastructure**
- 264 Python tests (up from 117) covering UI API, worker REST API, auth middleware, scanner metadata, queue pinning/retries, device poller IPv6 + name normalization, and more. ~55% server+client coverage baseline.
- 37 mocked Playwright browser tests covering devices, queue, workers tabs, editor modal, theme + responsive behavior.
- 16-target ESPHome compile matrix in CI (every push) — actual `esphome compile` runs against fixture YAMLs covering ESP8266, ESP32 (Arduino + IDF), ESP32-S2/S3/C3/C6 (IDF), RP2040, BK72xx, RTL87xx, plus complex configs (external components, packages, Bluetooth Proxy, Thread).
- CI also runs ruff lint, mypy on server + client, pytest with coverage reporting, frontend build, and Playwright tests on every push.
- Docker images now also published from `develop` (`ghcr.io/weirded/esphome-dist-{client,server}:develop`) so users can test unreleased changes without rebuilding locally. `:latest` stays pinned to `main`.

**Worker image versioning** ([#16](https://github.com/weirded/distributed-esphome/issues/16))
- Workers now report a Docker `IMAGE_VERSION` separate from the source code version. The server enforces a `MIN_IMAGE_VERSION` and refuses source-code auto-updates to workers running a stale image (since they'd just exec into a broken state).
- Workers tab shows a red "image stale" badge next to outdated workers, clickable to open the Connect Worker modal with the latest `docker run` command.

**Build worker uses psutil**
- Worker system info (memory, CPU usage, disk) now comes from psutil instead of hand-rolled `/proc` parsing. Cross-platform (Windows works as a bonus) and more accurate CPU utilization.

**Bug fixes**
- Fixed duplicate device rows for Thread-only and statically-IP'd devices ([#2](https://github.com/weirded/distributed-esphome/issues/2)). The scanner now resolves addresses through ESPHome's full `wifi → ethernet → openthread` precedence chain (each honoring `use_address` → `manual_ip.static_ip` → `{name}.local`), and the device poller correctly handles IPv6 mDNS records and merges discovery into existing YAML-derived rows by normalized name.
- Fixed `esphome run` prompting interactively when the worker host has multiple upload targets ([#22](https://github.com/weirded/distributed-esphome/issues/22)). The `--device` flag is now always passed (using the literal `"OTA"` when no specific address is known) so ESPHome never blocks waiting for stdin.
- Fixed OTA-only retries crashing with `unrecognized arguments: --no-logs` ([#21](https://github.com/weirded/distributed-esphome/issues/21)). `esphome upload` doesn't accept that flag — only `esphome run` does. The retry path now invokes `upload` without it.
- Fixed streamer mode not blurring IPs on unmanaged devices ([#19](https://github.com/weirded/distributed-esphome/issues/19)).
- Fixed Workers tab showing "up 5m" for offline workers based on stale process uptime. Now shows "offline for Xm" using the last heartbeat timestamp.
- Fixed queue duration showing the worker's compile time instead of wall-clock time. Now `Took 2m 14s` from enqueue to finish, and `Elapsed 45s` for in-progress jobs.
- Fixed queue sort defaulting to time instead of state — running jobs are now back at the top by default.
- Fixed validation request results showing the duplicate enqueue time twice in the queue.

**UI improvements**
- New "IP source" label under each device IP showing how the address was resolved: `via mDNS`, `wifi.use_address`, `wifi static_ip`, `ethernet static_ip`, `openthread.use_address`, or `{name}.local`. mDNS only "wins" over the default — explicit user choices stay authoritative because that mismatch is itself useful information.
- Queue tab: separate "Start Time" and "Finish Time" columns with absolute HH:MM:SS plus relative duration.
- "Clean Cache" button on online workers (per-worker) and "Clean All Caches" in the Workers tab header to clear stale ESPHome version caches without restarting workers.
- "Show unmanaged devices" toggle in the Devices column picker to hide mDNS discoveries with no matching config.
- Retry button now also available on successful jobs (not just failed) for the "I want to re-run this exactly" case.
- Worker compile commands now logged in the user-visible job log (cyan text) so bug reports include the exact command that ran.
- Image-stale badges turn the version cell red and link directly to the Connect Worker modal.

**Security hardening**
- Timing-safe Bearer token comparison (`secrets.compare_digest`) instead of `==`.
- Bounded log storage: worker-streamed logs capped at 512 KB per job, truncated with a marker (prevents OOM from runaway build output).
- `max_parallel_jobs` validation on worker registration (0–32, was unbounded).

**Codebase cleanup**
- New `helpers.py` (server) consolidates `safe_resolve`, `json_error`, `clamp`, `constant_time_compare` — replaces ~80 lines of inline path-traversal/error-response/auth code.
- Worker system info code extracted from `client.py` into `sysinfo.py`.
- Server constants (header names, supervisor IP, `secrets.yaml`) moved to `constants.py`.
- Test anti-patterns cleaned up: removed redundant `sys.path` from 7 test files, replaced hardcoded `/tmp` with `tmp_path`, converted queue tests to native async.

**Reorganized dev plans**
- Moved roadmap, release process, security audit, and per-release work-item files into a new `dev-plans/` directory.
- Released versions live under `dev-plans/archive/`.

## 1.2.0

**Built-in Local Worker** ([#4](https://github.com/weirded/distributed-esphome/issues/4))
- The add-on now includes a built-in build worker — no external Docker container required to get started
- Starts paused (0 slots); increase via the Workers tab to activate
- Great for HaOS setups where adding Docker containers is difficult

**Choose Which Worker Compiles** ([#5](https://github.com/weirded/distributed-esphome/issues/5))
- New "Upgrade on..." submenu in the device menu lets you pin a compile job to a specific worker
- Useful for debugging or when certain configs only work on specific hardware

**Docker Compose Support** ([#8](https://github.com/weirded/distributed-esphome/issues/8))
- Added `docker-compose.worker.yml` for easy worker deployment

**Configurable Device Columns**
- New columns: Area, Comment, Project (extracted from your YAML configs)
- Gear icon column picker to show/hide columns; preferences saved across sessions

**Redesigned UI**
- Modern design system with consistent buttons, modals, dropdowns, and badges
- Upgrade options consolidated into a single dropdown (All, All Online, Outdated, Selected)
- Device menu restructured into Device actions, Config actions, and worker submenu
- Search boxes on all three tabs (Devices, Queue, Workers)
- Queue actions grouped into Retry and Clear dropdowns
- Close button on all modals
- Copy to Clipboard button on compile and live log modals

**Worker Improvements**
- Simplified worker management: set slots to 0 to pause (removed separate Disable button)
- Disk space reporting with color warnings when running low
- Automatic cleanup of unused ESPHome versions when disk space is low
- Built-in worker highlighted and pinned to top of list

**Streamer Mode**
- New toggle in header blurs IPs, tokens, and sensitive data — useful for streams and screenshots

**Device Config Improvements**
- Better metadata extraction for configs using git packages (area, comment, project)
- Configs with substitution variables now resolve correctly in the device list

**Other Improvements**
- Validation output opens directly without cluttering the job queue
- "Version" column (renamed from "Running") shows firmware version more clearly
- Archived configs can be restored via new API endpoints
- Stale queue entries auto-cleaned after 1 hour
- Pinned worker preserved when retrying failed jobs

**Bug Fixes**
- Fixed OTA always using known device IP address
- Fixed timezone mismatch causing unnecessary recompiles
- Fixed editor content sometimes being wiped during poll cycles
- Fixed duplicate devices appearing after rename
- Fixed HA status not matching devices with non-standard entity names
- Fixed ESPHome install errors not showing in job log

## 1.1.0
Major update: React UI rewrite, ESPHome dashboard-grade features, Home Assistant integration.

**New React UI**
- Complete rewrite from vanilla JS to React + Vite + TypeScript
- Monaco YAML editor with ESPHome schema-aware autocomplete (697 components from installed package)
- Per-component config var suggestions fetched from schema.esphome.io
- !secret autocomplete from secrets.yaml, inline YAML syntax validation
- Save & Upgrade button (save + compile + OTA in one click)
- Unsaved change highlighting with line-level diff indicators
- Dark/light theme toggle with localStorage persistence
- Device search/filter bar across all columns

**Device Lifecycle**
- Rename device: updates config file, esphome.name, triggers compile+OTA to flash new name
- Delete device: archive to .archive/ or permanent delete with confirmation dialog
- Restart device via native ESPHome API (aioesphomeapi button_command) with HA REST fallback

**Live Device Logs**
- WebSocket streaming via aioesphomeapi with full ANSI color support in xterm.js
- Boot log included (dump_config=True)
- Timestamps on each log line [HH:MM:SS]
- Works with encrypted API connections (noise_psk)

**Compile Improvements**
- Switched to `esphome run --no-logs` (single process compile+OTA, matches native ESPHome UI)
- Colorized compile logs: INFO=green, WARNING=yellow, ERROR=red
- OTA retry with 5s delay on failure (keeps job in WORKING state for proper re-queuing)
- Server timezone passed to workers (prevents config_hash mismatch and unnecessary clean rebuilds)
- OTA always uses explicit --device with known IP address
- ESPHome install errors now visible in streaming job log

**Home Assistant Integration**
- Background poller detects ESPHome devices registered in HA via template API + /api/states
- MAC-based device matching (queries HA device connections) — most reliable method
- Name-based fallback: friendly_name, esphome.name, filename stem, MAC fragment matching
- HA column in Devices tab shows configured status (Yes/—)
- HA connectivity (_status binary_sensor) feeds into online/offline column
- Device restart via HA REST API as fallback when native API unavailable

**Config Validation**
- Validate button saves editor content first, then runs esphome config
- Validation opens streaming log modal directly (no toast intermediary)
- Badge shows Validating/Valid/Failed status in queue

**Performance**
- Concurrent device polling via asyncio.gather (all devices checked in parallel)
- HA entity poller runs immediately on startup (no 30s delay)
- Config resolution caches git clones (skip_update=True after first resolution)
- PyPI version list increased from 10 to 50

**UI Polish**
- Per-row Clear button in queue tab
- Edit buttons in queue rows and log modal header
- Hamburger menu redesigned: vertical ellipsis icon, plain text styling
- Live Logs and Restart moved to hamburger menu (never grayed out)
- Light mode: dark header for ESPHome logo readability, themed form inputs
- "Checking..." state with pulsing dot on startup (instead of showing offline)
- Copy API Key, Rename, Delete in device hamburger menu

**Operations**
- Suppressed aioesphomeapi.connection warnings (expected when devices offline)
- ESPHome add-on version detection at DEBUG level (no log spam)
- Debug endpoint GET /ui/api/debug/ha-status for HA matching troubleshooting
- Queue remove-by-ID endpoint for per-job clearing

**Bug Fixes**
- 89 bugs tracked and fixed during development (see BUGS.md)
- Fixed polling interval explosion (React useEffect dependency bug)
- Fixed editor content wiped on parent re-render (useRef pattern)
- Fixed disabled button CSS specificity (!important on all disabled properties)
- Fixed duplicate devices after rename (old entry removed from poller)
- Fixed modal closing on drag-select (mousedown target tracking)
- Fixed DeprecationWarning on app state mutation (clear+update pattern)

## 1.0.0
First stable release. Distributed ESPHome compilation with a full web UI.

**Distributed Compilation**
- Job queue with PENDING → WORKING → SUCCESS/FAILED state machine
- Performance-based job scheduling (fastest idle worker first, spread evenly)
- Workers report CPU benchmark, real-time utilization, system info
- Effective score = perf_score × (1 - cpu_usage/100) for load-aware scheduling

**Web UI**
- Three tabs: Devices, Queue, Workers
- xterm.js live log viewer with WebSocket streaming and ANSI support
- Monaco YAML editor with basic keyword completion
- ESPHome version dropdown (detect from HA add-on, select from PyPI)
- Connect Worker modal with configurable docker run command generator
- Auto-reload UI on server version change (X-Server-Version header)

**Device Management**
- mDNS device discovery + ping fallback + wifi.use_address support
- Device-to-config matching using ESPHome's full config resolution pipeline
- Encrypted API connections (extracts api.encryption.key from configs)
- Config change detection (file mtime vs device compilation time)
- Proactive device entries for use_address configs (no mDNS required)
- HA ESPHome add-on version detection via Supervisor API

**Build Workers**
- Docker-based remote workers with auto-update
- System info reporting (CPU, memory, OS, architecture, uptime)
- Persistent worker identity across restarts
- Clean deregistration on shutdown (SIGTERM handler)
- OTA firmware upload with retry and network diagnostics on failure
- OTA retry jobs pinned to original worker (PlatformIO cache reuse)

**Operations**
- Resolved config caching (mtime-based, eliminates repeated git clones)
- Suppressed noisy HTTP access logs
- hassio_api integration for ESPHome version detection
- host_network for mDNS device discovery
- Multi-arch Docker images (amd64 + arm64) published to GHCR

## Pre-1.0 Development History

See git history for detailed changes during the 0.0.1–0.0.73 development period.
