import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { ChevronDown, ChevronRight, History as HistoryIcon } from 'lucide-react';

import {
  getJobHistory,
  type JobHistoryEntry,
} from '@/api/client';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type { Target } from '@/types';
import { getJobBadge } from '@/utils/jobState';

// JH.7: fleet-wide compile history modal.
//
// Opened from a "History" button in the Queue tab toolbar. Reads from
// /ui/api/history with optional target / state / window filters. Read-only
// — no Retry / Cancel / Clear / Download buttons live here; the live
// Queue tab is the surface for those.
//
// Intentionally a shadcn Dialog (not a Sheet and not a new top-level
// tab). The tab bar answers "what's happening right now"; fleet-wide
// history is a "look at the past" action the user reaches for
// occasionally. A modal frames it as a task, closes cleanly, and
// doesn't compete with the live-state tabs.

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Used to populate the target filter dropdown with human-readable
   *  friendly names. Falls back to filename when friendly_name is unset. */
  targets: Target[];
}

const PAGE_SIZE = 100;

type StateFilter = '' | 'success' | 'failed' | 'timed_out' | 'cancelled';
type WindowDays = 1 | 7 | 30 | 90 | 365;


function formatRelative(epoch: number | null): string {
  if (epoch == null) return '—';
  const diff = Math.floor(Date.now() / 1000) - epoch;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatAbsolute(epoch: number | null): string {
  if (epoch == null) return '';
  const d = new Date(epoch * 1000);
  return d.toLocaleString();
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


export function QueueHistoryDialog({ open, onOpenChange, targets }: Props) {
  const [targetFilter, setTargetFilter] = useState<string>('');
  const [stateFilter, setStateFilter] = useState<StateFilter>('');
  const [windowDays, setWindowDays] = useState<WindowDays>(30);
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [nameQuery, setNameQuery] = useState('');

  const since = useMemo(() => {
    return Math.floor(Date.now() / 1000) - windowDays * 86400;
  }, [windowDays]);

  // Reset pagination when filters change.
  const filtersKey = `${targetFilter}|${stateFilter}|${windowDays}`;
  const [lastFiltersKey, setLastFiltersKey] = useState(filtersKey);
  if (lastFiltersKey !== filtersKey) {
    setLastFiltersKey(filtersKey);
    if (offset !== 0) setOffset(0);
    if (expandedId !== null) setExpandedId(null);
  }

  const swrKey = open
    ? ['queueHistory', targetFilter, stateFilter, since, offset]
    : null;
  const { data: rows, error, isLoading } = useSWR<JobHistoryEntry[]>(
    swrKey,
    () => getJobHistory({
      target: targetFilter || undefined,
      state: (stateFilter || undefined) as JobHistoryEntry['state'] | undefined,
      since,
      limit: PAGE_SIZE,
      offset,
    }),
    { revalidateOnFocus: false },
  );

  // Client-side friendly-name search. Doesn't go to the server because
  // the history endpoint only knows filenames; matching on friendly
  // names requires joining against the targets list which is already
  // in memory here.
  const visibleRows = useMemo(() => {
    if (!rows) return rows;
    if (!nameQuery.trim()) return rows;
    const q = nameQuery.toLowerCase();
    return rows.filter((r) => {
      if (r.target.toLowerCase().includes(q)) return true;
      const friendly = friendlyFor(targets, r.target).toLowerCase();
      return friendly.includes(q);
    });
  }, [rows, nameQuery, targets]);

  // Sorted target list for the filter dropdown.
  const targetOptions = useMemo(
    () =>
      [...targets]
        .sort((a, b) =>
          (a.friendly_name || a.target).localeCompare(b.friendly_name || b.target),
        ),
    [targets],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="dialog-xl">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <HistoryIcon className="size-4 text-[var(--text-muted)]" />
              Compile History
            </span>
          </DialogTitle>
        </DialogHeader>

        {/* Filter toolbar */}
        <div className="flex flex-wrap items-center gap-2 px-4 pt-2 pb-3 text-[12px]">
          <FilterSelect
            label="Target"
            value={targetFilter}
            onChange={setTargetFilter}
            options={[
              { value: '', label: 'All targets' },
              ...targetOptions.map((t) => ({
                value: t.target,
                label: t.friendly_name || t.target,
              })),
            ]}
          />
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
            label="Window"
            value={String(windowDays)}
            onChange={(v) => setWindowDays(Number(v) as WindowDays)}
            options={[
              { value: '1', label: 'Last 24 h' },
              { value: '7', label: 'Last 7 d' },
              { value: '30', label: 'Last 30 d' },
              { value: '90', label: 'Last 90 d' },
              { value: '365', label: 'Last year' },
            ]}
          />
          <div className="flex-1" />
          <Input
            type="search"
            placeholder="Search name…"
            className="h-7 w-[200px] text-[12px]"
            value={nameQuery}
            onChange={(e) => setNameQuery(e.target.value)}
          />
        </div>

        {/* Table body — sized relative to viewport so long histories scroll. */}
        <div className="max-h-[60vh] overflow-y-auto border-t border-[var(--border)]">
          {error && (
            <div className="m-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              Failed to load history: {(error as Error).message}
            </div>
          )}
          {isLoading && !rows && (
            <div className="p-4 text-xs text-[var(--text-muted)]">Loading…</div>
          )}
          {visibleRows && visibleRows.length === 0 && (
            <div className="p-6 text-center text-xs text-[var(--text-muted)]">
              No history matches the current filters.
            </div>
          )}
          {visibleRows && visibleRows.length > 0 && (
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-[var(--surface2)] text-[11px] text-[var(--text-muted)]">
                <tr>
                  <th className="w-5" />
                  <th className="px-2 py-1.5 text-left">Target</th>
                  <th className="px-2 py-1.5 text-left">State</th>
                  <th className="px-2 py-1.5 text-left">ESPHome</th>
                  <th className="px-2 py-1.5 text-left">Commit</th>
                  <th className="px-2 py-1.5 text-left">Duration</th>
                  <th className="px-2 py-1.5 text-left">Finished</th>
                  <th className="px-2 py-1.5 text-left">Trigger</th>
                  <th className="px-2 py-1.5 text-left">Worker</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const expanded = expandedId === row.id;
                  const hasExcerpt = !!row.log_excerpt;
                  const badge = getJobBadge({
                    state: row.state,
                    ota_result: row.ota_result ?? undefined,
                    validate_only: !!row.validate_only,
                    download_only: !!row.download_only,
                  });
                  return (
                    <>
                      <tr
                        key={row.id}
                        className={`border-t border-[var(--border)] ${hasExcerpt ? 'cursor-pointer hover:bg-[var(--surface2)]' : ''}`}
                        onClick={hasExcerpt ? () => setExpandedId((p) => (p === row.id ? null : row.id)) : undefined}
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
                          {friendlyFor(targets, row.target)}
                          {friendlyFor(targets, row.target) !== row.target && (
                            <span className="ml-1 text-[10px] text-[var(--text-muted)]">
                              {row.target}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-1.5"><span className={badge.cls}>{badge.label}</span></td>
                        <td className="px-2 py-1.5 font-mono text-[11px]">
                          {row.esphome_version || '—'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-[11px] text-[var(--text-muted)]" title={row.config_hash ?? undefined}>
                          {row.config_hash ? row.config_hash.slice(0, 7) : '—'}
                        </td>
                        <td className="px-2 py-1.5 tabular-nums">
                          {formatDuration(row.duration_seconds)}
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
                          <td colSpan={8} className="px-2 pb-3 pt-1">
                            <pre className="overflow-auto text-[11px] leading-snug font-mono text-[var(--text-muted)] max-h-[300px] whitespace-pre-wrap break-words">
                              {row.log_excerpt}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <DialogFooter>
          <div className="flex w-full items-center justify-between gap-2 px-1">
            <span className="text-[11px] text-[var(--text-muted)]">
              {rows
                ? `Showing ${visibleRows?.length ?? rows.length} of ${rows.length} in this page`
                : ''}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!rows || rows.length < PAGE_SIZE}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
