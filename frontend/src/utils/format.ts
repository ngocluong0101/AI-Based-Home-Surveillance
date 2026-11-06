export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export const percent = (x: number): string => `${Math.round(x * 100)}%`;

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;
}

/** "2024-05-01" from a date input to the ISO instant of local midnight; undefined for empty input. */
export function localDayStart(value: string, plusDays = 0): string | undefined {
  if (!value) return undefined;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d + plusDays).toISOString();
}

/** "just now", "5 min ago", "3 h ago", or the date for anything older than a day. */
export function relativeTime(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Local hour:minute:second of an instant. */
export function timeOfDay(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const localDayKey = (d: Date): string => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

/** "Today", "Yesterday", or "Mon, 3 Nov" (with the year when it is not this year): the heading of a day in a list. */
export function dayLabel(iso: string, now = Date.now()): string {
  const d = new Date(iso);
  const today = new Date(now);
  if (localDayKey(d) === localDayKey(today)) return "Today";
  if (localDayKey(d) === localDayKey(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1))) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: d.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

export interface DayGroup<T> {
  key: string;
  label: string;
  items: T[];
}

/** Items in the order given, split wherever the local day changes (a list newest-first gives the days newest-first). */
export function groupByDay<T extends { timestamp: string }>(items: T[], now = Date.now()): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  for (const item of items) {
    const key = localDayKey(new Date(item.timestamp));
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(item);
    else groups.push({ key, label: dayLabel(item.timestamp, now), items: [item] });
  }
  return groups;
}

export function clockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
