# Worker log visibility — design (WL.1 / WL.2 / WL.3)

Status: approved 2026-04-23 (brainstorming session), ready for implementation plan.

## Context

The Workers tab surfaces worker state (online, parallel slots, current job) but offers no way to see a worker's own log output. Compile-job logs already stream worker → server → UI via `/api/v1/jobs/{id}/log` + `/api/v1/jobs/{id}/log/ws` (rendered by `LogModal.tsx`), but everything *outside* a compile job — startup errors, heartbeat failures, ESPHome install failures, OTA retries, "worker won't register" first-setup symptoms — is only visible on the worker's host. For the server's spawned local worker it drowns in the add-on log; for remote workers (friend's Docker host, Raspberry Pi, remote-office laptop) the operator has no visibility without SSH.

Scope-fit per `USER_PERSONA.md` rule (d): shipping workers without a way to see why one is misbehaving is a first-setup operational break. WL is kept in 1.6.2 despite being a new user-visible capability because real users are stuck on "is this thing working?" with no recourse. The design reuses the compile-log transport and viewer to keep the new surface narrow.

## Non-goals

- Persistent worker-log history across restarts. Worker ring buffer is process-lifetime; server buffer TTLs out after 1 h of no watchers. Debug aid, not audit trail.
- Structured log querying (filter by level, grep, etc). Raw ANSI stream into xterm matches the compile-log UX; users can filter with Ctrl+F on the rendered terminal.
- Log forwarding to external sinks (syslog, Loki, Datadog). Out of scope; future work if the need surfaces.

## Key constraint: pull-when-watched

Logs only traverse the worker → server link while **a user is actively watching them**. When nobody has the dialog open, no log traffic on the network; heartbeat is untouched and still fires at its usual cadence (10 s default). The signalling mechanism is a boolean flag in the existing `HeartbeatResponse`, gated by a per-worker subscriber count on the server that's maintained by open UI WebSockets.

## Architecture

```
┌── UI ───────────────┐      WS (presence)      ┌── server ────────────────┐    HTTP heartbeat    ┌── worker ──────────┐
│ LogModal            │ ───────────────────────►│ WorkerLogBroker          │ ◄──────────────────► │ HeartbeatLoop      │
│ (kind: 'worker')    │ ◄────── log frames ──── │  - per-worker buffer     │    HTTP log push     │ LogCaptureHandler  │
│                     │                         │  - subscriber counter    │ ◄──────────────────  │  (2000-line ring)  │
└─────────────────────┘                         │  - sets stream_logs flag │                      └────────────────────┘
                                                └──────────────────────────┘
```

Five units with narrow interfaces:

### 1. Worker `LogCaptureHandler` (WL.1)

`logging.Handler` subclass attached to the root logger in `ha-addon/client/client.py`. Every emitted record is formatted through the existing formatter (so captured lines match stdout exactly — `%(asctime)s %(levelname)-8s v<ver> %(ctx)s%(name)s: %(message)s`) and appended to a `collections.deque(maxlen=2000)` plus a monotonic byte-offset counter. Thread-safe via a `threading.Lock` — the handler is called from the main thread, the heartbeat thread, and each worker-slot job thread. Always on: the 2-MB ceiling is cheap insurance so the backlog is ready whenever the server asks. Does not suppress the existing `StreamHandler` — lines still go to stdout for host-side observability.

### 2. Worker heartbeat integration (WL.2 — worker side)

Heartbeat loop at `client.py:352-416` already parses `HeartbeatResponse`. New branch: when `response.stream_logs` is truthy, ensure a log-pusher thread is running; when falsy (or the transition goes true → false), signal that thread to exit. `None` means "unchanged" — default state is off.

The pusher is a separate daemon thread:

```
while stream_logs_event.is_set():
    chunk, last_offset = handler.drain_since(acked_offset)
    if chunk:
        try:
            post("/api/v1/workers/{client_id}/logs",
                 WorkerLogAppend(offset=acked_offset, lines=chunk).model_dump())
            acked_offset = last_offset
        except HTTPError:
            # keep chunk; retry next tick. don't advance acked_offset.
            pass
    sleep(1.0)
```

`drain_since` returns everything in the ring buffer with `byte_offset >= acked`. On process start both offsets are 0; on first push after a watch begins, the whole ring dumps in one chunk (up to ~2 MB, bounded by `WorkerLogAppend` body cap of 4× `MAX_LOG_BYTES`).

### 3. Server `WorkerLogBroker` (WL.2 — server side)

New module `ha-addon/server/worker_log_broker.py`. State keyed by `client_id`:

```
_buffers:      dict[client_id, deque[str]]   # 2000 lines each
_next_offset:  dict[client_id, int]          # byte-offset the server expects for the NEXT push
                                             # (i.e. previous push's offset + len(lines));
                                             # resets to 0 on restart detection; absent
                                             # entry is treated as 0
_subscribers:  dict[client_id, set[WebSocketResponse]]
_evict_tasks:  dict[client_id, asyncio.Task]  # 1 h no-watchers eviction
```

Offset math: worker sends `offset = N` for a chunk of length `L`. Server accepts and sets `_next_offset[id] = N + L` iff `N == _next_offset.get(id, 0)` (happy path, no gap) or `N < _next_offset.get(id, 0)` (restart — write separator, reset `_next_offset[id] = L`, and accept). A push with `N > _next_offset.get(id, 0)` means the worker advanced past a chunk the server never saw (network loss mid-retry); accept it, update offset, and log a warning — no attempt at gap recovery since the ring is already bounded.

Public surface:

| Method | Who calls | Purpose |
|---|---|---|
| `is_watched(client_id) -> bool` | `api.py` heartbeat handler | Sets `HeartbeatResponse.stream_logs` |
| `append(client_id, offset, lines)` | POST `/api/v1/workers/{id}/logs` handler | Dedupe by offset; detect restart if offset < `_offsets[client_id]`; write synthetic `\x1b[2m--- worker restarted ---\x1b[0m\n` separator; broadcast to open WS subscribers |
| `snapshot(client_id) -> str` | GET `/ui/api/workers/{id}/logs` | Returns everything currently buffered; used by the UI for the initial hydration |
| `subscribe(client_id, ws)` | WS handler on `/ui/api/workers/{id}/logs/ws` open | Increments count; cancels any pending evict task |
| `unsubscribe(client_id, ws)` | WS close handler | Decrements count; schedules a 1 h eviction task if count hits 0 |

The broker is **independent** of the `Worker` registry. Separation of concerns: registry tracks liveness + config, broker tracks log transport. Worker dataclass is unchanged.

### 4. Server HTTP/WS endpoints

- `POST /api/v1/workers/{client_id}/logs` — worker push. Bearer auth (same as heartbeat). Body size cap mirrors `/api/v1/jobs/{id}/log`: 4× `MAX_LOG_BYTES`. Parses `WorkerLogAppend`, forwards to `broker.append`.
- `GET /ui/api/workers/{client_id}/logs` — UI initial hydration. HA-ingress trust. Returns the broker buffer as plain text (ANSI preserved) with `Content-Type: text/plain; charset=utf-8`.
- `GET /ui/api/workers/{client_id}/logs/ws` — UI live tail. HA-ingress trust. WebSocket; server sends raw chunks as they arrive from pushes. Same shape as `/api/v1/jobs/{id}/log/ws`.
- `main.py` heartbeat handler (`api.py:151-186`) gains one line: `resp.stream_logs = broker.is_watched(client_id)`.

### 5. UI — parametrize `LogModal`, don't fork

`LogModal.tsx`'s `jobId: string` prop becomes a tagged union:

```ts
type LogSource =
  | { kind: 'job'; jobId: string }
  | { kind: 'worker'; workerId: string };

interface LogModalProps {
  source: LogSource;
  ...
}
```

URL builder inside the component keys off `source.kind`:

- `kind: 'job'` → `/ui/api/jobs/{jobId}/log` + `/ui/api/jobs/{jobId}/log/ws` (existing).
- `kind: 'worker'` → `/ui/api/workers/{workerId}/logs` + `/ui/api/workers/{workerId}/logs/ws`.

Everything else — xterm lifecycle, WS-with-polling-fallback, ANSI colouring, "Download logs" button, sizing, close behaviour — is shared. This is the single most important piece of the design: we end up with one log viewer, not two.

`WorkersTab.tsx` gains a row-level dropdown-menu item "View logs" opening `<LogModal source={{ kind: 'worker', workerId: w.client_id }} ... />`. Dropdown `open` state is lifted to the parent component keyed on `client_id`, mirroring `QueueTab.tsx`'s `downloadMenuOpenJobId` pattern (bugs #2 and #71); the 1 Hz SWR poll would otherwise tear the menu down mid-click.

## Boundary semantics

The four moments that need explicit behaviour:

1. **User opens the dialog.** UI does two things in parallel: `GET /ui/api/workers/{id}/logs` (hydrates xterm with whatever's in the server buffer — empty on first watch ever, populated if this worker was watched in the last hour) and opens the WS. Between dialog-open and the first push-frame after the heartbeat round-trip (up to 10 s), a hint line renders inside the xterm viewer: `\x1b[2mWaiting for worker to start streaming… (up to 10 s)\x1b[0m`. Cleared by the first push frame.

2. **First push after `stream_logs` flips true.** Worker's pusher has `acked_offset=0`; sends the entire ring buffer contents in one `WorkerLogAppend`. Server stores it, fans to WS subscribers. Subsequent pushes carry only new lines.

3. **User closes the dialog / crashes the tab.** WS close → `broker.unsubscribe` → subscriber count for that worker drops to 0 → pending eviction task scheduled for 1 h out (cancelled if any new watcher arrives). Next worker heartbeat (within 10 s) returns `stream_logs=false`; pusher thread exits cleanly.

4. **Worker restarts mid-watch.** Old subprocess dies; new one starts with a new ring buffer, offsets reset to 0. On re-registration the server keeps its per-worker buffer (same `client_id`). Next push arrives with `offset=0`, which is less than the server's last-seen offset → broker detects restart, writes the `--- worker restarted ---` separator into the buffer, fans to WS subscribers, then appends the new push. No explicit `session_id` field needed.

## Data shapes — protocol additions (all additive, no `PROTOCOL_VERSION` bump)

```python
# ha-addon/server/protocol.py (+ byte-identical client copy — PY-6)

class HeartbeatResponse(_ProtocolMessage):
    # existing fields unchanged ...
    stream_logs: Optional[bool] = None
    # None → unchanged from prior heartbeat; True → start pushing; False → stop pushing.

class WorkerLogAppend(_ProtocolMessage):
    offset: int    # byte-offset of first byte in `lines` since worker process start
    lines: str     # pre-formatted, ANSI-colored, newline-terminated (same shape as JobLogAppend.lines)
```

Adherence to `PY-6`:

- Every model base is `_ProtocolMessage` with `extra="ignore"`, so older peers drop unknown fields silently.
- Fields added here are all `Optional` / additive.
- `tests/test_protocol.py` assertion that server and client protocol files are byte-identical remains green (same diff applied to both copies in the same commit).

No change to `PROTOCOL_VERSION`.

## Tests

- **`tests/test_worker_log_buffer.py`** — worker-side `LogCaptureHandler`: overflow drops oldest lines, monotonic byte-offset counter survives `drain_since` calls, handler does **not** suppress the existing stdout `StreamHandler` (propagation preserved), thread-safe append + drain under 50×50 `ThreadPoolExecutor` contention.

- **`tests/test_worker_log_broker.py`** — server-side broker: `subscribe`/`unsubscribe` counter math, `is_watched` flips at 0↔1, buffer eviction triggers 1 h after last unsubscribe (inject a fake clock), eviction is cancelled if a new subscriber arrives in the window, restart detection writes the separator and resets `_offsets` on a backwards-moving push, oversized-body rejection (4× `MAX_LOG_BYTES`) matches the existing job-log rejection path byte-for-byte.

- **`tests/test_worker_log_protocol.py`** — heartbeat round-trip with `stream_logs` present/absent/None; `WorkerLogAppend` round-trip; oversized-payload rejection; older-peer compatibility (construct a `HeartbeatResponse` with unknown fields, assert `extra="ignore"` still drops them).

- **`ha-addon/ui/e2e/worker-logs-dialog.spec.ts`** — mocked Playwright: open dropdown in WorkersTab → click "View logs" → dialog renders with seeded hydration snapshot → simulated WS push appends lines → "Download logs" produces a file with `content-disposition: attachment; filename="worker-<hostname>-<iso-ts>.log"`. Also: a regression case that walks the dropdown-open-state-lifting pattern under a 1 Hz SWR poll (bug #2 / #71 class guard).

- Update `ha-addon/ui/e2e/` existing specs that touch WorkersTab rows if the dropdown API changes.

## Critical files

Server-side new / changed:

- `ha-addon/server/worker_log_broker.py` — new module (§3).
- `ha-addon/server/protocol.py` — add `stream_logs`, `WorkerLogAppend` (§Data shapes).
- `ha-addon/client/protocol.py` — byte-identical mirror of the above.
- `ha-addon/server/api.py:151-186` — heartbeat handler adds `resp.stream_logs = broker.is_watched(...)`; new handler for `POST /api/v1/workers/{id}/logs`.
- `ha-addon/server/ui_api.py` — new handlers for `GET /ui/api/workers/{id}/logs` and WS `/ui/api/workers/{id}/logs/ws`.
- `ha-addon/server/main.py` — instantiate broker, wire into app state, register WS route.

Client-side new / changed:

- `ha-addon/client/client.py` — `LogCaptureHandler` class, attach to root logger, pusher-thread lifecycle, heartbeat branch on `stream_logs`.

UI new / changed:

- `ha-addon/ui/src/components/LogModal.tsx` — parametrize via `source: LogSource` tagged union. All existing callsites (primary: `QueueTab.tsx` for the compile-log flow) get updated in the same commit to pass `source={{ kind: 'job', jobId }}`. No deprecation wrapper — churn is small and a wrapper would split the log-viewer contract across two prop shapes.
- `ha-addon/ui/src/components/WorkersTab.tsx` — add "View logs" dropdown item; lift open state to parent.
- `ha-addon/ui/src/api/client.ts` — URL builders for the two new worker-log endpoints.

Tests as listed above.

## Verification — end-to-end

1. Deploy `./push-to-hass-4.sh`; confirm CI green including the four new test files.
2. In the Workers tab, open "View logs" on the local worker. Expected: xterm renders immediately with whatever the local worker has logged since its last 1 h idle window (likely its startup banner + heartbeat chatter). Within 10 s, live 1 Hz tail begins.
3. Close dialog, watch `docker exec addon_local_esphome_dist_server ss -tnp` — no lingering connections, WS cleanly released. Heartbeat cadence on server logs drops to normal (10 s) immediately; `stream_logs` in the response flips false within one round-trip.
4. Open dialog again — hydration pulls the server buffer (still present within 1 h), live tail resumes.
5. Stop the local worker via Workers-tab controls; start it. Open the logs dialog. Expected: `--- worker restarted ---` separator appears in xterm, followed by the new worker's startup output.
6. Exercise the remote path: from `docker-optiplex-5` standalone deployment, open "View logs" against the `docker-optiplex-5-worker`. Same behaviour.

## Risk and migration

- Protocol: additive; existing workers (pre-WL) run unchanged and simply never opt into streaming because `HeartbeatResponse.stream_logs` is an unknown field they drop via `extra="ignore"`. Server treats their `is_watched` as usual but their pusher thread never exists, so no pushes arrive — users just see an empty log dialog. Upgrade the worker image and it starts working.
- Memory: 2000 lines × up to 1 KB avg × worst-case 16 workers = ~32 MB worker-side, same on server. Bounded. Local worker accounts for 2 of those by design (server-side buffer + worker-subprocess ring).
- Latency: open-to-first-frame up to 10 s in the worst case (user opens right after a heartbeat). Acceptable — UX shows the "waiting…" hint in xterm during the gap.
- Failure modes: if the push endpoint returns 5xx, the pusher retries next tick with the same `acked_offset`; lines never drop on the happy path. If 5xx errors persist, lines are bounded by the 2000-line ring — oldest fall off silently. If a worker is offline during a watch, server's `is_watched` stays true, no pushes arrive, WS subscribers see nothing new but keep the hydrated snapshot.
- Server restart mid-watch: broker state is in-memory, so `_next_offset` and `_buffers` are lost. First push after restart arrives with `offset=N` and no server state → accepted as happy-path (absent entry treated as 0 → gap path → accepted with a warning, no restart separator). Existing WS subscribers reconnect on transient WS errors via the same path `LogModal` already uses for job logs; no new reconnection logic.

## Out of scope — tracked elsewhere

- CI gate around the coverage this adds: treat as extension of the existing `pytest`/Playwright gates; no new GitHub Actions workflow.
- Standalone-mode regression coverage: HT.14 already exercises the Workers tab; worker-logs-dialog e2e under `e2e/` (mocked) is sufficient. No new `e2e-hass-4` spec needed for 1.6.2.
