import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select } from './ui/select';

/**
 * Convert the friendly interval picker state to a 5-field cron expression.
 */
function buildCron(interval: string, every: number, time: string, dow: string): string {
  const [hh, mm] = time.split(':').map(Number);
  const minute = isNaN(mm) ? 0 : mm;
  const hour = isNaN(hh) ? 2 : hh;

  switch (interval) {
    case 'hours':
      return every === 1 ? `${minute} * * * *` : `${minute} */${every} * * *`;
    case 'days':
      return every === 1 ? `${minute} ${hour} * * *` : `${minute} ${hour} */${every} * *`;
    case 'weeks':
      return `${minute} ${hour} * * ${dow}`;
    default:
      return `${minute} ${hour} * * *`;
  }
}

/**
 * Try to parse a cron expression back into the friendly picker state.
 * Returns null if the expression can't be represented by the picker.
 */
function parseCron(cron: string): { interval: string; every: number; time: string; dow: string } | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [min, hour, dom, _mon, dow] = parts;
  void _mon;

  const minute = parseInt(min, 10);
  if (isNaN(minute)) return null;

  // Hours: "M */N * * *"
  if (hour.startsWith('*/') && dom === '*' && dow === '*') {
    const n = parseInt(hour.slice(2), 10);
    return { interval: 'hours', every: n, time: `00:${String(minute).padStart(2, '0')}`, dow: '0' };
  }
  // Every hour: "M * * * *"
  if (hour === '*' && dom === '*' && dow === '*') {
    return { interval: 'hours', every: 1, time: `00:${String(minute).padStart(2, '0')}`, dow: '0' };
  }

  const h = parseInt(hour, 10);
  if (isNaN(h)) return null;
  const timeStr = `${String(h).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;

  // Days: "M H */N * *" or "M H * * *"
  if (dow === '*') {
    if (dom === '*') return { interval: 'days', every: 1, time: timeStr, dow: '0' };
    if (dom.startsWith('*/')) {
      const n = parseInt(dom.slice(2), 10);
      return { interval: 'days', every: n, time: timeStr, dow: '0' };
    }
    return null;
  }
  // Weeks: "M H * * D"
  if (dom === '*') {
    return { interval: 'weeks', every: 1, time: timeStr, dow };
  }
  return null;
}

interface Props {
  target: string;
  displayName: string;
  currentSchedule?: string | null;
  currentEnabled?: boolean;
  currentOnce?: string | null;  // ISO datetime for one-time schedule
  onSave: (cron: string) => void;
  onSaveOnce: (datetime: string) => void;  // ISO datetime
  onDelete: () => void;
  onToggle: () => void;
  onClose: () => void;
}

const DAY_OPTIONS = [
  { label: 'Sunday', value: '0' },
  { label: 'Monday', value: '1' },
  { label: 'Tuesday', value: '2' },
  { label: 'Wednesday', value: '3' },
  { label: 'Thursday', value: '4' },
  { label: 'Friday', value: '5' },
  { label: 'Saturday', value: '6' },
];

export function ScheduleModal({
  target: _target,
  displayName,
  currentSchedule,
  currentEnabled,
  currentOnce,
  onSave,
  onSaveOnce,
  onDelete,
  onToggle,
  onClose,
}: Props) {
  void _target;

  // Try to parse the current schedule into picker state; fall back to defaults.
  const parsed = currentSchedule ? parseCron(currentSchedule) : null;

  const [mode, setMode] = useState<'friendly' | 'cron' | 'once'>(
    currentOnce ? 'once' : (parsed || !currentSchedule ? 'friendly' : 'cron'),
  );
  const [interval, setInterval] = useState(parsed?.interval ?? 'days');
  const [every, setEvery] = useState(parsed?.every ?? 1);
  const [time, setTime] = useState(parsed?.time ?? '02:00');
  const [dow, setDow] = useState(parsed?.dow ?? '0');
  const [rawCron, setRawCron] = useState(currentSchedule ?? '');
  // #17: one-time schedule — datetime-local string (YYYY-MM-DDTHH:MM).
  const [onceDate, setOnceDate] = useState(() => {
    if (currentOnce) {
      // Convert ISO string to datetime-local format
      const d = new Date(currentOnce);
      return d.toISOString().slice(0, 16);
    }
    // Default: tomorrow at 02:00
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(2, 0, 0, 0);
    return tomorrow.toISOString().slice(0, 16);
  });

  const hasSchedule = !!(currentSchedule || currentOnce);

  const effectiveCron = mode === 'cron'
    ? rawCron.trim()
    : buildCron(interval, every, time, dow);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent style={{ maxWidth: 480 }}>
        <DialogHeader>
          <DialogTitle>Schedule Upgrade — {displayName}</DialogTitle>
        </DialogHeader>
        <div className="p-[18px] flex flex-col gap-4">
          {/* Mode toggle */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">Mode</span>
            <div className="flex gap-0 border border-[var(--border)] rounded-[var(--radius)] overflow-hidden">
              <Button
                variant={mode === 'friendly' ? 'default' : 'secondary'}
                size="xs"
                style={{ borderRadius: 0, border: 'none' }}
                onClick={() => setMode('friendly')}
              >
                Simple
              </Button>
              <Button
                variant={mode === 'cron' ? 'default' : 'secondary'}
                size="xs"
                style={{ borderRadius: 0, border: 'none', borderLeft: '1px solid var(--border)' }}
                onClick={() => { setMode('cron'); if (!rawCron) setRawCron(effectiveCron); }}
              >
                Cron
              </Button>
              <Button
                variant={mode === 'once' ? 'default' : 'secondary'}
                size="xs"
                style={{ borderRadius: 0, border: 'none', borderLeft: '1px solid var(--border)' }}
                onClick={() => setMode('once')}
              >
                Once
              </Button>
            </div>
          </div>

          {mode === 'once' ? (
            <div>
              <label className="block text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)] mb-1">
                Date &amp; Time
              </label>
              <Input
                type="datetime-local"
                value={onceDate}
                min={new Date().toISOString().slice(0, 16)}
                onChange={e => setOnceDate(e.target.value)}
              />
              <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                The device will be upgraded once at this date and time, then the schedule is automatically removed.
              </div>
            </div>
          ) : mode === 'friendly' ? (
            <>
              {/* Interval picker */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[12px]">Every</span>
                <Input
                  type="number"
                  min={1}
                  max={30}
                  value={every}
                  onChange={e => setEvery(Math.max(1, parseInt(e.target.value, 10) || 1))}
                  className="w-[60px]"
                />
                <Select value={interval} onChange={e => setInterval(e.target.value)} className="w-[100px]">
                  <option value="hours">hour(s)</option>
                  <option value="days">day(s)</option>
                  <option value="weeks">week(s)</option>
                </Select>
                {interval === 'weeks' && (
                  <>
                    <span className="text-[12px]">on</span>
                    <Select value={dow} onChange={e => setDow(e.target.value)} className="w-[120px]">
                      {DAY_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
                    </Select>
                  </>
                )}
                {interval !== 'hours' && (
                  <>
                    <span className="text-[12px]">at</span>
                    <Input
                      type="time"
                      value={time}
                      onChange={e => setTime(e.target.value)}
                      className="w-[100px]"
                    />
                  </>
                )}
              </div>
              <div className="text-[11px] text-[var(--text-muted)]">
                Cron: <code className="bg-[var(--surface)] px-1 rounded">{effectiveCron}</code>
              </div>
            </>
          ) : (
            <div>
              <label className="block text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)] mb-1">
                Cron Expression
              </label>
              <Input
                type="text"
                value={rawCron}
                placeholder="0 2 * * *"
                onChange={e => setRawCron(e.target.value)}
              />
              <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                Standard 5-field cron: minute hour day-of-month month day-of-week
              </div>
            </div>
          )}

          {hasSchedule && (
            <div className="flex items-center gap-2">
              <label className="text-[12px] text-[var(--text)]">
                <input
                  type="checkbox"
                  checked={currentEnabled ?? false}
                  onChange={onToggle}
                  className="mr-2"
                />
                Schedule enabled
              </label>
            </div>
          )}

          <div className="flex justify-between items-center pt-2">
            <div>
              {hasSchedule && (
                <Button variant="destructive" size="sm" onClick={onDelete}>
                  Remove Schedule
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
              <Button
                size="sm"
                disabled={mode === 'once' ? !onceDate : !effectiveCron}
                onClick={() => {
                  if (mode === 'once') {
                    onSaveOnce(new Date(onceDate).toISOString());
                  } else {
                    onSave(effectiveCron);
                  }
                }}
              >
                {mode === 'once'
                  ? 'Schedule Once'
                  : hasSchedule ? 'Update Schedule' : 'Set Schedule'}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
