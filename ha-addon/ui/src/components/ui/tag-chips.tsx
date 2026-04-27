import { Tag, X } from 'lucide-react';

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

const HUES = [0, 30, 60, 95, 130, 160, 190, 215, 240, 270, 300, 335];

function tagHueIndex(tag: string): number {
  let h = 5381;
  for (let i = 0; i < tag.length; i++) {
    h = ((h << 5) + h + tag.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % HUES.length;
}

function tagChipStyle(tag: string): { background: string; borderColor: string; color: string } {
  const hue = HUES[tagHueIndex(tag)];
  return {
    background: `hsl(${hue}, 65%, 45%)`,
    borderColor: `hsl(${hue}, 70%, 35%)`,
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
}

export function TagChip({ tag, onRemove, onClick }: ChipProps) {
  const s = tagChipStyle(tag);
  const interactive = onClick != null;
  return (
    <span
      className={
        'inline-flex items-center gap-0.5 rounded-full border px-1.5 py-px text-[10px] leading-none ' +
        (interactive ? 'cursor-pointer hover:opacity-90 transition-opacity' : '')
      }
      style={s}
      title={`Tag: ${tag}`}
      onClick={onClick}
      role={interactive ? 'button' : undefined}
    >
      <Tag className="size-2.5" aria-hidden="true" />
      {tag}
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          aria-label={`Remove tag ${tag}`}
          className="ml-0.5 inline-flex size-3 items-center justify-center rounded-full bg-white/20 hover:bg-white/40"
          tabIndex={-1}
        >
          <X className="size-2" />
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
