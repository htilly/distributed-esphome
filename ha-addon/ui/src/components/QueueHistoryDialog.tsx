import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, History as HistoryIcon } from 'lucide-react';

import {
  getJobHistory,
  type JobHistoryEntry,
} from '@/api/client';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type { Target } from '@/types';
import { getJobBadge } from '@/utils/jobState';
import { renderAnsi } from '@/utils/ansi';

// JH.7: fleet-wide compile history modal.
//
// Opened from a "History" button in the Queue tab toolbar. Reads from
// /ui/api/history; filters live client-side on top of paged server
// fetches. Read-only — no Retry / Cancel / Clear / Download buttons
// live here; the live Queue tab is the surface for those.
//
// Design choices that evolved via bugs #40, #43, #44, #46:
//   - No target dropdown. Universal search across device / target /
//     ESPHome version / commit hash / trigger / worker / state / message
//     covers the same cases with less chrome.
//   - No window-days dropdown. Instead a "from / to" pair of
//     ``<input type=datetime-local>`` with quick presets for 24h / 7d
//     / 30d / 90d / 1y. Honest about being native datetimes rather
//     than faking Grafana — no extra JS dep needed for the use case.
//   - Infinite scroll, not Previous/Next. IntersectionObserver on a
//     sentinel below the table fetches the next page when it scrolls
//     into view. 100 rows per page.

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Used to resolve filenames to human-readable friendly names. */
  targets: Target[];
  /**
   * Bug #41: click on a commit-hash cell opens the AV.6 History panel
   * preset to ``from = hash, to = Current``. Threaded through so this
   * dialog can share the Queue tab's existing "Diff since compile"
   * flow without reinventing it.
   */
  onOpenHistoryDiff?: (target: string, fromHash: string) => void;
}

const PAGE_SIZE = 100;

type StateFilter = '' | 'success' | 'failed' | 'timed_out' | 'cancelled';
type Preset = '24h' | '7d' | '30d' | '90d' | '1y' | 'custom';

const PRESETS: { value: Preset; label: string; seconds: number | null }[] = [
  { value: '24h', label: 'Last 24 h', seconds: 86_400 },
  { value: '7d', label: 'Last 7 d', seconds: 7 * 86_400 },
  { value: '30d', label: 'Last 30 d', seconds: 30 * 86_400 },
  { value: '90d', label: 'Last 90 d', seconds: 90 * 86_400 },
  { value: '1y', label: 'Last year', seconds: 365 * 86_400 },
  { value: 'custom', label: 'Custom…', seconds: null },
];

// --------------------------------------------------------------------- //

function formatRelative(epoch: number | null): string {
  if (epoch == null) return '—';
  const diff = Math.floor(Date.now() / 1000) - epoch;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86_400)}d ago`;
}

function formatAbsolute(epoch: number | null): string {
  if (epoch == null) return '';
  return new Date(epoch * 1000).toLocaleString();
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m ${s}s`;
}

function triggeredLabel(row: JobHistoryEntry): string {
  if (row.triggered_by === 'ha_action') return 'HA';
  if (row.triggered_by === 'schedule') {
    return row.trigger_detail === 'once' ? 'Scheduled·1x' : 'Scheduled';
  }
  return 'User';
}

function friendlyFor(targets: Target[], filename: string): string {
  const t = targets.find((x) => x.target === filename);
  return t?.friendly_name || t?.device_name || filename;
}

// ``<input type=datetime-local>`` takes ``YYYY-MM-DDTHH:mm`` in local tz.
function epochToDatetimeLocal(epoch: number): string {
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function datetimeLocalToEpoch(s: string): number | null {
  if (!s) return null;
  const d = new Date(s);
  const t = Math.floor(d.getTime() / 1000);
  return Number.isFinite(t) ? t : null;
}


// --------------------------------------------------------------------- //

export function QueueHistoryDialog({ open, onOpenChange, targets, onOpenHistoryDiff }: Props) {
  // Bug #44: time range state. `preset` is the selected pill; `from`/`to`
  // are epoch seconds — null means "open-ended on this side".
  const [preset, setPreset] = useState<Preset>('30d');
  const [customFrom, setCustomFrom] = useState<number | null>(null);
  const [customTo, setCustomTo] = useState<number | null>(null);

  const [stateFilter, setStateFilter] = useState<StateFilter>('');

  // Bug #40: universal client-side search across every textual column.
  const [q, setQ] = useState('');

  // Bug #46: infinite-scroll page accumulator. Each entry is one
  // server-fetched page; flat list is `pages.flat()`. offset for the
  // next page is `pages.length * PAGE_SIZE`.
  const [pages, setPages] = useState<JobHistoryEntry[][]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Derive the SWR-style key from the active filters. Whenever this
  // changes we reset the infinite-scroll accumulator.
  const filtersKey = useMemo(() => {
    const { since, until } = activeWindow(preset, customFrom, customTo);
    return `${since ?? ''}|${until ?? ''}|${stateFilter}`;
  }, [preset, customFrom, customTo, stateFilter]);

  useEffect(() => {
    if (!open) return;
    setPages([]);
    setHasMore(true);
    setError(null);
    setExpandedId(null);
  }, [filtersKey, open]);

  // Reset infinite-scroll state when the dialog closes so reopening
  // starts cleanly.
  useEffect(() => {
    if (!open) {
      setPages([]);
      setExpandedId(null);
    }
  }, [open]);

  async function fetchNextPage() {
    if (loading || !hasMore || !open) return;
    setLoading(true);
    try {
      const { since } = activeWindow(preset, customFrom, customTo);
      const offset = pages.length * PAGE_SIZE;
      const rows = await getJobHistory({
        state: (stateFilter || undefined) as JobHistoryEntry['state'] | undefined,
        since: since ?? undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setPages((prev) => [...prev, rows]);
      if (rows.length < PAGE_SIZE) setHasMore(false);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }

  // First load whenever the effective filter key changes.
  useEffect(() => {
    if (!open) return;
    if (pages.length === 0 && hasMore && !loading) {
      void fetchNextPage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pages.length, filtersKey]);

  // Flat list with the custom "until" filter applied (server doesn't
  // support until-side filtering) + client-side text search.
  const flatRows = useMemo(() => {
    const all = pages.flat();
    const { until } = activeWindow(preset, customFrom, customTo);
    let filtered = until != null ? all.filter((r) => (r.finished_at ?? 0) <= until) : all;
    const needle = q.trim().toLowerCase();
    if (needle) {
      filtered = filtered.filter((r) => rowMatchesSearch(r, needle, targets));
    }
    return filtered;
  }, [pages, preset, customFrom, customTo, q, targets]);

  // IntersectionObserver sentinel at the bottom of the list.
  const sentinelRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) void fetchNextPage();
    }, { rootMargin: '200px' });
    obs.observe(el);
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pages, hasMore, loading, filtersKey]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="dialog-xl"
        // Bug #45: fix the footer to the bottom edge even on a short
        // result set — flex column + min-height makes the results body
        // own the remaining space.
        style={{ height: 'min(80vh, 720px)', display: 'flex', flexDirection: 'column' }}
      >
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <HistoryIcon className="size-4 text-[var(--text-muted)]" />
              Compile History
            </span>
          </DialogTitle>
        </DialogHeader>

        {/* Filter toolbar */}
        <div className="flex flex-wrap items-center gap-2 px-4 pt-2 pb-3 text-[12px] shrink-0">
          <FilterSelect
            label="State"
            value={stateFilter}
            onChange={(v) => setStateFilter(v as StateFilter)}
            options={[
              { value: '', label: 'All states' },
              { value: 'success', label: 'Success' },
              { value: 'failed', label: 'Failed' },
              { value: 'timed_out', label: 'Timed out' },
              { value: 'cancelled', label: 'Cancelled' },
            ]}
          />

          <FilterSelect
            label="Time"
            value={preset}
            onChange={(v) => setPreset(v as Preset)}
            options={PRESETS.map((p) => ({ value: p.value, label: p.label }))}
          />

          {preset === 'custom' && (
            <>
              <label className="inline-flex items-center gap-1 text-[var(--text-muted)]">
                <span>From:</span>
                <input
                  type="datetime-local"
                  className="rounded-md border border-[var(--border)] bg-[var(--surface2)] px-2 py-0.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
                  value={customFrom != null ? epochToDatetimeLocal(customFrom) : ''}
                  onChange={(e) => setCustomFrom(datetimeLocalToEpoch(e.target.value))}
                />
              </label>
              <label className="inline-flex items-center gap-1 text-[var(--text-muted)]">
                <span>To:</span>
                <input
                  type="datetime-local"
                  className="rounded-md border border-[var(--border)] bg-[var(--surface2)] px-2 py-0.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
                  value={customTo != null ? epochToDatetimeLocal(customTo) : ''}
                  onChange={(e) => setCustomTo(datetimeLocalToEpoch(e.target.value))}
                />
              </label>
            </>
          )}

          <div className="flex-1" />
          <Input
            type="search"
            placeholder="Search device, target, version, worker, hash…"
            className="h-7 w-[260px] text-[12px]"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {/* Body — fills the flex column so the footer sticks. */}
        <div className="flex-1 min-h-0 overflow-y-auto border-t border-[var(--border)]">
          {error && (
            <div className="m-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              Failed to load history: {error.message}
            </div>
          )}

          {flatRows.length === 0 && !loading && (
            <div className="p-6 text-center text-xs text-[var(--text-muted)]">
              No history matches the current filters.
            </div>
          )}

          {flatRows.length > 0 && (
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-[var(--surface2)] text-[11px] text-[var(--text-muted)]">
                <tr>
                  <th className="w-5" />
                  {/* Bug #42: "Device", not "Target". The column shows the
                      friendly device name primarily, with the filename as a
                      suffix — users think "devices", not "target files". */}
                  <th className="px-2 py-1.5 text-left">Device</th>
                  <th className="px-2 py-1.5 text-left">State</th>
                  <th className="px-2 py-1.5 text-left">ESPHome</th>
                  <th className="px-2 py-1.5 text-left">Commit</th>
                  <th className="px-2 py-1.5 text-left">Duration</th>
                  {/* Bug #39: started column in addition to finished. */}
                  <th className="px-2 py-1.5 text-left">Started</th>
                  <th className="px-2 py-1.5 text-left">Finished</th>
                  <th className="px-2 py-1.5 text-left">Trigger</th>
                  <th className="px-2 py-1.5 text-left">Worker</th>
                </tr>
              </thead>
              <tbody>
                {flatRows.map((row) => (
                  <HistoryTableRow
                    key={row.id}
                    row={row}
                    targets={targets}
                    expanded={expandedId === row.id}
                    onToggle={() => setExpandedId((p) => (p === row.id ? null : row.id))}
                    onOpenHistoryDiff={onOpenHistoryDiff}
                  />
                ))}
                {/* Bug #46: IntersectionObserver sentinel — when this row
                    scrolls into view, fetch the next page. */}
                {hasMore && (
                  <tr ref={sentinelRef}>
                    <td colSpan={10} className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">
                      {loading ? 'Loading…' : ''}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div className="shrink-0 border-t border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-[11px] text-[var(--text-muted)]">
          {flatRows.length === 0
            ? (loading ? 'Loading…' : 'No rows')
            : `Showing ${flatRows.length} row${flatRows.length === 1 ? '' : 's'}${hasMore ? '' : ' (end of history)'}`}
        </div>
      </DialogContent>
    </Dialog>
  );
}


// --------------------------------------------------------------------- //

function activeWindow(
  preset: Preset,
  customFrom: number | null,
  customTo: number | null,
): { since: number | null; until: number | null } {
  if (preset === 'custom') {
    return { since: customFrom, until: customTo };
  }
  const def = PRESETS.find((p) => p.value === preset);
  if (def?.seconds != null) {
    return { since: Math.floor(Date.now() / 1000) - def.seconds, until: null };
  }
  return { since: null, until: null };
}


function rowMatchesSearch(row: JobHistoryEntry, q: string, targets: Target[]): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  const fields: (string | null | undefined)[] = [
    row.target,
    friendlyFor(targets, row.target),
    row.state,
    row.esphome_version,
    row.config_hash,
    row.assigned_hostname,
    row.assigned_client_id,
    row.triggered_by,
    row.trigger_detail,
    row.ota_result,
    row.log_excerpt,
  ];
  return fields.some((f) => typeof f === 'string' && f.toLowerCase().includes(needle));
}


function HistoryTableRow({
  row,
  targets,
  expanded,
  onToggle,
  onOpenHistoryDiff,
}: {
  row: JobHistoryEntry;
  targets: Target[];
  expanded: boolean;
  onToggle: () => void;
  onOpenHistoryDiff?: (target: string, fromHash: string) => void;
}) {
  const hasExcerpt = !!row.log_excerpt;
  const badge = getJobBadge({
    state: row.state,
    ota_result: row.ota_result ?? undefined,
    validate_only: !!row.validate_only,
    download_only: !!row.download_only,
  });
  const friendly = friendlyFor(targets, row.target);
  return (
    <>
      <tr
        className={`border-t border-[var(--border)] ${hasExcerpt ? 'cursor-pointer hover:bg-[var(--surface2)]' : ''}`}
        onClick={hasExcerpt ? onToggle : undefined}
      >
        <td className="pl-2 py-1.5">
          {hasExcerpt ? (
            expanded ? (
              <ChevronDown className="size-3.5 text-[var(--text-muted)]" />
            ) : (
              <ChevronRight className="size-3.5 text-[var(--text-muted)]" />
            )
          ) : null}
        </td>
        <td className="px-2 py-1.5 truncate max-w-[220px]" title={row.target}>
          {friendly}
          {friendly !== row.target && (
            <span className="ml-1 text-[10px] text-[var(--text-muted)]">{row.target}</span>
          )}
        </td>
        <td className="px-2 py-1.5"><span className={badge.cls}>{badge.label}</span></td>
        <td className="px-2 py-1.5 font-mono text-[11px]">{row.esphome_version || '—'}</td>
        <td className="px-2 py-1.5 font-mono text-[11px]">
          {row.config_hash ? (
            onOpenHistoryDiff ? (
              <button
                type="button"
                className="text-[var(--text-muted)] underline-offset-2 hover:underline cursor-pointer"
                title={`Diff since this compile: ${row.config_hash}`}
                onClick={(e) => { e.stopPropagation(); onOpenHistoryDiff(row.target, row.config_hash!); }}
              >
                {row.config_hash.slice(0, 7)}
              </button>
            ) : (
              <span className="text-[var(--text-muted)]" title={row.config_hash}>
                {row.config_hash.slice(0, 7)}
              </span>
            )
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          )}
        </td>
        <td className="px-2 py-1.5 tabular-nums">{formatDuration(row.duration_seconds)}</td>
        <td className="px-2 py-1.5 text-[var(--text-muted)] tabular-nums" title={formatAbsolute(row.started_at)}>
          {row.started_at ? formatRelative(row.started_at) : '—'}
        </td>
        <td className="px-2 py-1.5 text-[var(--text-muted)] tabular-nums" title={formatAbsolute(row.finished_at)}>
          {formatRelative(row.finished_at)}
        </td>
        <td className="px-2 py-1.5">{triggeredLabel(row)}</td>
        <td className="px-2 py-1.5 text-[var(--text-muted)] truncate max-w-[140px]" title={row.assigned_hostname ?? undefined}>
          {row.assigned_hostname || '—'}
        </td>
      </tr>
      {expanded && hasExcerpt && (
        <tr className="bg-[var(--surface2)]">
          <td />
          <td colSpan={9} className="px-2 pb-3 pt-1">
            <pre className="overflow-auto text-[11px] leading-snug font-mono text-[var(--text)] max-h-[300px] whitespace-pre-wrap break-words">
              {/* Bug #36: render ANSI SGR codes in the stored excerpt. */}
              {renderAnsi(row.log_excerpt ?? '')}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}


function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="inline-flex items-center gap-1 text-[var(--text-muted)]">
      <span>{label}:</span>
      <select
        className="rounded-md border border-[var(--border)] bg-[var(--surface2)] px-2 py-0.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] cursor-pointer"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
