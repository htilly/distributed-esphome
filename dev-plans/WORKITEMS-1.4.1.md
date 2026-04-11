# Work Items — 1.4.1

Theme: **UI quality sprint.** A focused hardening pass on the frontend to close the gaps surfaced by the 1.4.0 post-release UI audit. No new user-visible features. Goal: split the DevicesTab god component, replace the hand-rolled context menu with the shadcn wrapper, close the accessibility gaps, clean up the API layer, and raise e2e coverage on the flows that shipped in 1.4.0 without tests.

Source material: comprehensive UI audit covering component library consistency, state management, type safety, accessibility, and testing coverage. 27 findings prioritized across 7 phases, ordered to minimize rework (quick wins first, DevicesTab split in the middle — it unlocks everything touching that file).

## Phase 1 — Quick wins

- [ ] **QS.1 Delete dead file `src/lib/utils.ts`** — contains only unused `clsx`/`cn` re-exports (grep confirms zero imports). Scaffold leftover from shadcn.
- [ ] **QS.2 Icon-only buttons: add `aria-label`** — 5 buttons currently read as emoji glyphs or silence to screen readers:
  - `App.tsx:449` theme toggle (☀/☾)
  - `App.tsx:464` streamer mode toggle (👁/🔒)
  - `DevicesTab.tsx:700` row hamburger (⋮)
  - `DevicesTab.tsx:854` column picker (⚙)
  - `EsphomeVersionDropdown.tsx:56` refresh (↻)
- [ ] **QS.3 Convert `<span onClick>` to `<button>`** — 4 violations of the "semantic HTML" design judgment rule:
  - `App.tsx:437` Secrets button
  - `App.tsx:449-463` theme toggle
  - `App.tsx:464-470` streamer mode toggle
  - `DevicesTab.tsx:408` SortHeader (folded into QS.15 below)
- [ ] **QS.4 Fix non-null assertion in `getApiKey()`** — `api/client.ts:237` uses `data.key!` without validation. Crashes at call site with a confusing message if the server omits `key`. Replace with explicit null check + thrown `Error`.
- [ ] **QS.5 Add `compile_target` to `Device` type** — `types/index.ts` Device interface is missing `compile_target?: string | null`, but `DevicesTab.tsx:333` reads it. Document the Device-vs-Target distinction in JSDoc ("Device is mDNS-discovered runtime state; Target is YAML-managed compile metadata").
- [ ] **QS.6 Remove SWR `deepCompare` using `JSON.stringify`** — `App.tsx:88` serializes the entire response to a string on every 1Hz poll for workers/devices/queue. At scale (100+ devices) this is hot-path CPU waste that defeats SWR's built-in memoization. Remove the custom `compare`, let SWR's default shallow compare handle it.
- [ ] **QS.7 SWR `onError` — log at minimum** — `App.tsx:93,98,108,113,120` all silently swallow errors. When the server goes down, the UI shows stale data forever. At minimum, `console.error('SWR', key, err)`. Stretch: top-of-page banner when `serverInfo` SWR has an error set.

## Phase 2 — API layer cleanup

- [ ] **QS.8 Extract `parseResponse<T>` helper** — every POST endpoint in `api/client.ts` repeats the same ~3-line error-handling pattern (lines 111-112, 122-123, 133-134, 148-149, 164-165, etc.). Extract:
  ```typescript
  async function parseResponse<T>(r: Response): Promise<T> {
    const data = (await r.json().catch(() => ({}))) as { error?: string } & T;
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data as T;
  }
  ```
  Reduces ~150 lines of boilerplate.
- [ ] **QS.9 Define response types at module top** — replace inline `as { enqueued: number }` / `as { cancelled: number }` casts with named interfaces (`CompileResponse`, `CancelResponse`, `RetryResponse`, `ValidateResponse`, `ApiKeyResponse`, etc.). Self-documents the wire contract.
- [ ] **QS.10 Propagate server error details in getX() functions** — `getTargets`, `getDevices`, `getWorkers`, `getQueue` currently throw generic `"Failed to fetch X"` on non-OK responses, losing server-provided error text. Apply the same pattern as the POST endpoints.

## Phase 3 — Component hygiene

- [ ] **QS.11 Extract `<Label>` component** — `components/ui/label.tsx`. The pattern `className="block text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)] mb-1"` appears 10+ times across `UpgradeModal`, `ConnectWorkerModal`, and `ScheduleModal`. Extract as a shadcn-style Label with optional `size` prop, plus proper `htmlFor`/`id` association with inputs (currently labels are siblings without association — accessibility gap).
- [ ] **QS.12 Replace raw `<input>` in RenameModal** — `DevicesTab.tsx:198-204` uses raw `<input>` with inline style object. Swap for `<Input>` wrapper.
- [ ] **QS.13 Add `<ButtonGroup>` component (or `variant="group"`)** — shell toggle in `ConnectWorkerModal.tsx:218,226` and mode toggle in `ScheduleModal.tsx:157,164,171` both use `<Button style={{ borderRadius: 0, border: 'none' }}>` to fake a button group. Extract `components/ui/button-group.tsx` with proper first/last/middle styling.
- [ ] **QS.14 Audit and convert inline `style={{ ... }}` to Tailwind** — 25+ instances found. Worst offenders:
  - `ConnectWorkerModal.tsx:212-226` shell toggle container
  - `ScheduleModal.tsx:157-171` mode toggle
  - `DeviceLogModal.tsx:112,123`
  - `WorkersTab.tsx:30,40,47,55,58`
  - `QueueTab.tsx:158,206`
  - `EsphomeVersionDropdown.tsx:22,38-42`
  - `StatusDot.tsx:15` (conditional color via style instead of className)
- [ ] **QS.15 Icon strategy decision + rollout** — currently mixes Lucide (in `ui/checkbox.tsx`, `ui/dropdown-menu.tsx`), emoji (📌 🕐 ☀ ☾ 👁 🔒), and HTML entities (`&#8635;`, `&#9660;`, `&#8595;`, `&#8942;`, `&#9881;`, `&#x2715;`). Decide: adopt Lucide universally (Pin, Clock, Sun, Moon, Eye, Lock, RefreshCw, ChevronDown, Download, MoreVertical, Settings, X) or accept emoji and drop HTML entities. Document the choice in CLAUDE.md Design Judgment.

## Phase 4 — DevicesTab split

The big one. The current `DevicesTab.tsx` is **1,173 lines with 24 hooks** and carries an `// eslint-disable-next-line react-hooks/exhaustive-deps` at line 705 because the column `useMemo` deps can't be correctly maintained. This phase unblocks most of the remaining 1.4.0 feature work (DO.5 group-by, DO.6 filter chips, DO.7 bulk tag ops all touch this file).

- [ ] **QS.16 Replace hand-rolled context menu with `<DropdownMenu>`** — `DevicesTab.tsx:557-589` is a fixed-overlay `<div>` with manually positioned `<button>` children, manual backdrop + z-index, and ad-hoc hover states. Refactor to `<DropdownMenu>` + `<DropdownMenuTrigger>` + `<DropdownMenuItem>`. Migrate items: Live Logs, Restart Device, Copy API Key, Schedule Upgrade, Pin to current version, Rename, Delete. Drop the manual positioning logic entirely (Base UI handles it). **Most visible CLAUDE.md "Default to shadcn/ui" violation.**
- [ ] **QS.17 Extract `useDeviceColumns()` hook** — move the 378-line column definitions `useMemo` into `deviceTableColumns.ts` as a hook. Accept callbacks as parameters, memoize internally. Lets us legitimately declare `workers` and `onCompile` as dependencies (removing the ESLint disable).
- [ ] **QS.18 Extract `DeviceTableActions.tsx`** — bulk actions dropdown ("Actions ▾" button), schedule-selected handler, remove-schedule-selected handler, bulk compile.
- [ ] **QS.19 Extract `DeviceTableModals.tsx`** — RenameModal and DeleteModal (already nested subcomponents inside DevicesTab.tsx:178-266). Just move them to their own file.
- [ ] **QS.20 Memoize inline handler props** — `App.tsx:443-446` `onRefresh={async () => { ... }}` and `DevicesTab.tsx:805-827` dropdown item `onClick` handlers. Wrap in `useCallback` with proper deps. Do this in the same pass as QS.17-19 to avoid churning the same file twice.
- [ ] **QS.21 Add `aria-sort` to SortHeader** — `DevicesTab.tsx:393-412`. Fix once in the SortHeader component, cascades to all 11 sortable columns. Also wrap the click target in a `<button>` (fixes QS.3 for this file).

After this phase: `DevicesTab.tsx` should be ~300-400 lines (orchestrator: state + table + filter), the hooks should be under ~10, and the ESLint disable at line 705 should be gone.

## Phase 5 — EditorModal + utils

- [ ] **QS.22 Split `EditorModal.tsx` Monaco setup into `editor/` submodule** — 610 lines currently mixing Monaco setup, schema fetching, completion provider, validation. Extract:
  - `editor/monacoSetup.ts` — language registration, theme, schema URL wiring
  - `editor/completionProvider.ts` — completion provider registration (move the module-level `_currentEsphomeVersion` / `_componentList` / `_componentListPromise` here as a proper singleton, with JSDoc explaining why)
  - `editor/useYamlValidation.ts` — validation hook
  EditorModal stays as the dialog wrapper + state orchestrator.
- [ ] **QS.23 Split `src/utils.ts` grab-bag** — 90 lines mixing formatting (`timeAgo`, `stripYaml`, `fmtDuration`), job-state predicates (5 functions), and UI-specific logic (`getJobBadge`, `BADGE_VARIANTS`). Split into:
  - `utils/format.ts`
  - `utils/jobState.ts`
  - `utils/cron.ts` (move `formatCronHuman()` out of DevicesTab)
  Move `BADGE_VARIANTS` into `components/ui/badge.tsx` or the call site.
- [ ] **QS.24 Remove dead `_onRename` parameter in `EditorModal.tsx:232`** — "kept for API compatibility" but no caller passes it. Grep and remove.

## Phase 6 — Tests and safety net

- [ ] **QS.25 Add missing e2e coverage** — mocked Playwright tests for the user flows that shipped in 1.4.0 but weren't tested:
  - Device rename (RenameModal)
  - Device delete (DeleteModal)
  - Version pin / unpin
  - Upgrade modal worker + version selection
  - Schedule modal friendly picker + cron mode
  - Bulk "Schedule Selected" / "Remove Schedule Selected"
  - Worker cache clean
  - Column visibility persistence across reloads
  - Theme persistence across reloads
- [ ] **QS.26 Add React Error Boundary around `<App />`** — currently any component crash takes down the entire UI. Minimal boundary that renders a "Something went wrong — reload" card with the error message in `<details>`. Place in `App.tsx` wrapping the root `<div>`.

## Phase 7 — Optional polish

- [ ] **QS.27 Optional polish** — lower-priority items; only pull in if the sprint has slack:
  - `ConnectWorkerModal.tsx:71-83` — 8 separate useState → single `useReducer`
  - `types/index.ts` `address_source` → string union type
  - `LogModal.tsx:43,50` — document why manual `setInterval` is needed (separate from SWR polling, for elapsed-time display)
  - Persist DevicesTab sort order in localStorage
  - URL query param state for deep-linking (`?tab=queue&filter=kitchen`)

## Open Bugs & Tweaks

