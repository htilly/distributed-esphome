import { X } from 'lucide-react';

/**
 * TG.5 / TG.6 — render a worker or device tag as a colored chip pill.
 *
 * Bug #6: each tag picks a color from a 12-hue palette via a stable djb2
 * hash of its text. Same tag string → same color across rows / tabs /
 * pages so "kitchen" looks the same on a device row as on a worker row,
 * and "prod" stays visually distinct from "linux".
 *
 * Bug #12: GitHub-issue-label-style solid chips (saturated mid-tone bg
 * with white text) instead of washed-out pastels. The single palette
 * works on both ``[data-theme="light"]`` and ``[data-theme="dark"]``
 * surfaces because ``hsl(h, 65%, 45%)`` is dark enough for white text
 * to read AA-contrast and saturated enough to register as the chip's
 * color (not gray) against either surface.
 *
 * Bug #11: ``TagChip`` accepts an optional ``onRemove`` handler — passing
 * one renders a small × inside the chip so the editor can mutate. Without
 * it the chip is a plain read-only pill, identical to what the table
 * cells render.
 */

// Bug #21: 12 *visually-distinct* named colors instead of 12 evenly-spaced
// HSL hues. The dev.6 hue list (0/30/60/…) had clusters that looked
// near-identical at chip size — 130+160 both read as "green",
// 215+240 both read as "blue", etc. Using Tailwind's 600-stop palette
// (curated for accessible foreground/background contrast against white)
// gives every chip a perceptibly different hue and saturation, so a
// row of 4 tags reads as 4 colors at a glance.
const PALETTE: { bg: string; border: string }[] = [
  { bg: '#dc2626', border: '#991b1b' }, // red-600
  { bg: '#ea580c', border: '#9a3412' }, // orange-600
  { bg: '#ca8a04', border: '#854d0e' }, // amber-600
  { bg: '#65a30d', border: '#3f6212' }, // lime-600
  { bg: '#16a34a', border: '#15803d' }, // green-600
  { bg: '#0d9488', border: '#115e59' }, // teal-600
  { bg: '#0284c7', border: '#075985' }, // sky-600
  { bg: '#2563eb', border: '#1e40af' }, // blue-600
  { bg: '#7c3aed', border: '#5b21b6' }, // violet-600
  { bg: '#c026d3', border: '#86198f' }, // fuchsia-600
  { bg: '#db2777', border: '#9d174d' }, // pink-600
  { bg: '#475569', border: '#334155' }, // slate-600
];

function tagHueIndex(tag: string): number {
  // djb2 hash — small, stable, no deps. Same tag string always picks
  // the same palette entry, regardless of where it's rendered.
  let h = 5381;
  for (let i = 0; i < tag.length; i++) {
    h = ((h << 5) + h + tag.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % PALETTE.length;
}

function tagChipStyle(tag: string): { background: string; borderColor: string; color: string } {
  const c = PALETTE[tagHueIndex(tag)];
  return {
    background: c.bg,
    borderColor: c.border,
    color: '#ffffff',
  };
}

interface ChipProps {
  tag: string;
  /** Bug #11: render an inline × the caller can wire to "remove this tag". */
  onRemove?: () => void;
  /** Optional click handler for the whole chip (e.g. picker suggestions
   *  call ``addTag(t)`` when the user clicks). Mutually exclusive with
   *  ``onRemove`` in practice — a chip is either "click body to add" or
   *  "click × to remove". */
  onClick?: () => void;
  /** TG.5/TG.6 filter pill: optional usage count rendered as a faint
   *  ``(N)`` suffix. Doesn't participate in the color hash so the same
   *  tag stays the same color across rows / pill-bar / editor. */
  count?: number;
}

export function TagChip({ tag, onRemove, onClick, count }: ChipProps) {
  const s = tagChipStyle(tag);
  const interactive = onClick != null;
  return (
    <span
      className={
        // Bug #15: drop the Lucide Tag icon — the chip color + text is
        // already a strong "this is a tag" signal, the icon just ate
        // horizontal space in narrow table cells.
        // Bug #16: bumped to 12px / 1.5 line-height for legibility now
        // that the column has more room.
        'inline-flex items-center rounded-full border px-2 py-0.5 text-[12px] leading-tight ' +
        (interactive ? 'cursor-pointer hover:opacity-90 transition-opacity' : '')
      }
      style={s}
      title={`Tag: ${tag}`}
      onClick={onClick}
      role={interactive ? 'button' : undefined}
    >
      {tag}
      {count != null && (
        <span className="ml-1 opacity-70 text-[10px]">({count})</span>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          aria-label={`Remove tag ${tag}`}
          className="ml-1 inline-flex size-3.5 items-center justify-center rounded-full bg-white/20 hover:bg-white/40"
          tabIndex={-1}
        >
          <X className="size-2.5" />
        </button>
      )}
    </span>
  );
}

export function TagChips({ tags }: { tags: string[] | null | undefined }) {
  if (!tags || tags.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {tags.map(t => <TagChip key={t} tag={t} />)}
    </span>
  );
}
