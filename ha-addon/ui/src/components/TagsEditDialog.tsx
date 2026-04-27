import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { TagChip } from './ui/tag-chips';

/**
 * TG.5 / TG.6 / bug #11 — chip-input tag editor used by both the Workers
 * and Devices tabs.
 *
 * Each existing tag renders as a colored chip with an inline × to remove
 * it. The trailing input lets the user type a new tag (Enter or comma to
 * commit, Backspace on empty input drops the last chip). Below the input
 * a "Suggestions" row lists fleet-wide tags not yet attached — clicking
 * one adds it. Suggestions filter to substring matches as the user types
 * so "ki…" narrows the pool to "kitchen".
 *
 * Bug #14: the seed-from-``initial`` effect only fires on the open
 * transition (false→true), not on every parent re-render — the parent
 * tab polls SWR at 1Hz and would otherwise wipe in-progress edits.
 */

interface Props {
  /** Open/close state owned by the parent (so the parent can lift it out
   *  of any TanStack row cell — same `actionsMenuOpenClientId` pattern as
   *  the Workers Actions menu, see CLAUDE.md "Lift DropdownMenu open state
   *  out of any row cell"). */
  open: boolean;
  onOpenChange: (open: boolean) => void;

  /** Human-readable subject line — "Worker macdaddy" / "Device kitchen.yaml". */
  subject: string;

  /** Current tag list — controlled value the dialog seeds with on each
   *  open transition. */
  initial: string[];

  /** Fleet-wide pool of known tags (caller computes the union of every
   *  device + worker's ``tags`` array). The dialog filters out anything
   *  already on this entry and shows the remainder as clickable
   *  suggestions; substring match against the input prefix narrows them
   *  further. Empty array = no suggestions row. */
  suggestions: string[];

  /** Save handler. Receives the final tag list (no leading/trailing
   *  whitespace; duplicates already dropped). Throws on failure; the
   *  dialog catches and surfaces the error inline without closing. */
  onSave: (tags: string[]) => Promise<void>;
}

export function TagsEditDialog({ open, onOpenChange, subject, initial, suggestions, onSave }: Props) {
  const [tags, setTags] = useState<string[]>(initial);
  const [input, setInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Bug #14: re-seed only on the open transition, not on every render.
  // ``initial`` is a fresh array reference each parent SWR poll even when
  // the values are unchanged; depending on it would wipe in-progress edits.
  useEffect(() => {
    if (open) {
      setTags(initial);
      setInput('');
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const addTag = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    setTags(prev => (prev.includes(v) ? prev : [...prev, v]));
    setInput('');
    inputRef.current?.focus();
  };

  const removeTag = (t: string) => {
    setTags(prev => prev.filter(x => x !== t));
    inputRef.current?.focus();
  };

  // Suggestions: pool minus already-attached, optionally filtered by the
  // prefix the user has typed. Capped at 12 to keep the dialog bounded.
  const filtered = useMemo(() => {
    const sel = new Set(tags);
    const q = input.trim().toLowerCase();
    return suggestions
      .filter(s => !sel.has(s))
      .filter(s => !q || s.toLowerCase().includes(q))
      .slice(0, 12);
  }, [suggestions, tags, input]);

  const handleSave = async () => {
    // If the user typed a tag but didn't press Enter / comma, treat it
    // as the last chip rather than dropping it on save.
    const finalTags = input.trim() && !tags.includes(input.trim())
      ? [...tags, input.trim()]
      : tags;

    setSaving(true);
    setError(null);
    try {
      await onSave(finalTags);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save tags');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        {/* shadcn DialogHeader is flex-row justify-between — keep it title-only.
            Description + body content live in the padded body section below
            so they don't collide horizontally with the title. Bug #17. */}
        <DialogHeader>
          <DialogTitle>Edit tags — {subject}</DialogTitle>
        </DialogHeader>

        <div className="px-4 py-3 space-y-3">
          <p className="text-[12px] text-[var(--text-muted)]">
            Type a tag and press Enter or comma to add. Click × to remove.
            Click a suggestion below to add a tag already in use elsewhere
            in the fleet.
          </p>

          {/* Chip-input box: existing tags + trailing free-form input. */}
          <div
            className="flex flex-wrap items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface2)] px-2 py-1.5 min-h-[40px] cursor-text"
            onClick={() => inputRef.current?.focus()}
          >
            {tags.map(t => (
              <TagChip key={t} tag={t} onRemove={() => removeTag(t)} />
            ))}
            <input
              ref={inputRef}
              autoFocus
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ',') {
                  e.preventDefault();
                  if (input.trim()) addTag(input);
                  else if (!saving) void handleSave();
                } else if (e.key === 'Backspace' && !input && tags.length > 0) {
                  e.preventDefault();
                  setTags(prev => prev.slice(0, -1));
                }
              }}
              placeholder={tags.length === 0 ? 'Type a tag and press Enter…' : ''}
              className="flex-1 min-w-[140px] bg-transparent outline-none text-[13px] text-[var(--text)] placeholder:text-[var(--text-muted)]"
            />
          </div>

          {/* Suggestions: fleet-wide tags not yet attached, filtered by input prefix. */}
          {filtered.length > 0 && (
            <div>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Suggestions
              </div>
              <div className="flex flex-wrap gap-1.5">
                {filtered.map(t => (
                  <TagChip key={t} tag={t} onClick={() => addTag(t)} />
                ))}
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-[var(--danger,#ef4444)] bg-[var(--danger,#ef4444)]/10 px-2.5 py-1.5 text-[12px] text-[var(--danger,#ef4444)]">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="secondary" size="sm" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
