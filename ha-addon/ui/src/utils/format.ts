/**
 * General text/time formatting helpers (QS.23).
 *
 * Split out of the former `src/utils.ts` grab-bag so the job-state predicates
 * and cron-expression helper can live in siblings without pulling in every
 * unrelated function.
 */

export function timeAgo(isoString: string): string {
  const ago = Math.round((Date.now() - new Date(isoString).getTime()) / 1000);
  if (ago < 60) return ago + 's ago';
  if (ago < 3600) return Math.floor(ago / 60) + 'm ago';
  return Math.floor(ago / 3600) + 'h ago';
}

export function stripYaml(s: string | undefined | null): string {
  return s ? s.replace(/\.ya?ml$/i, '') : (s ?? '');
}

export function fmtDuration(secs: number | null | undefined): string {
  if (secs == null) return '—';
  // Bug #48: no fractional seconds anywhere in the app. Round to the
  // nearest second so the Queue tab, Job History drawers, Log modal,
  // and stats strips all format durations the same way.
  const s = Math.round(secs);
  if (s < 60) return s + 's';
  return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
}

/** Bug #48: consolidated epoch-seconds → "Xago" relative time. */
export function fmtEpochRelative(epoch: number | null | undefined): string {
  if (epoch == null) return '—';
  const diff = Math.floor(Date.now() / 1000) - epoch;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86_400)}d ago`;
}

/** Bug #48: consolidated epoch-seconds → absolute locale-string. */
export function fmtEpochAbsolute(epoch: number | null | undefined): string {
  if (epoch == null) return '';
  return new Date(epoch * 1000).toLocaleString();
}

/**
 * Build an absolute URL for a Home Assistant deep-link (#35).
 *
 * When the add-on is loaded via HA Ingress (the primary deployment), the
 * parent window is HA itself, so we use `window.top.location.origin`. When
 * accessed directly on the add-on's port (e.g. http://hass-4.local:8765),
 * we fall back to the same hostname on the default HA port 8123.
 *
 * Returns null if window.top access throws (cross-origin) and we can't
 * derive a reasonable fallback.
 */
export function haDeepLink(path: string): string | null {
  try {
    if (typeof window === 'undefined') return null;
    const top = window.top;
    if (top && top !== window) {
      try {
        return `${top.location.origin}${path}`;
      } catch {
        /* cross-origin parent — fall through */
      }
    }
    const loc = window.location;
    return `${loc.protocol}//${loc.hostname}:8123${path}`;
  } catch {
    return null;
  }
}
