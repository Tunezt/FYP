/** Day grouping for chronological lists — the day is the unit of history.
 *
 * All bucketing/labels are computed in the BUSINESS timezone (not the
 * browser's): the backend's "today" numbers use the business tz, and a sale at
 * 23:30 WIB must not jump to "besok" for an owner whose laptop is set to
 * Malaysia time. Pass `business.timezone`; falls back to the browser tz. */

function fmt(tz: string | undefined, opts: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("id-ID", { ...opts, ...(tz ? { timeZone: tz } : {}) });
}

/** YYYY-MM-DD in the given timezone (en-CA gives ISO-like ordering). */
function dayKey(d: Date, tz?: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    ...(tz ? { timeZone: tz } : {}),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function dayLabel(date: Date, tz?: string, now = new Date()): string {
  const key = dayKey(date, tz);
  if (key === dayKey(now, tz)) return "Hari ini";
  if (key === dayKey(new Date(now.getTime() - 86_400_000), tz)) return "Kemarin";
  const sameYear = key.slice(0, 4) === dayKey(now, tz).slice(0, 4);
  return fmt(tz, sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" }).format(date);
}

export function daySubLabel(date: Date, tz?: string, now = new Date()): string | null {
  // Weekday under a relative label; nothing when the label is already a date.
  const key = dayKey(date, tz);
  const isToday = key === dayKey(now, tz);
  const isYesterday = key === dayKey(new Date(now.getTime() - 86_400_000), tz);
  if (isToday || isYesterday) return fmt(tz, { weekday: "long" }).format(date);
  return null;
}

export function timeLabel(date: Date, tz?: string): string {
  return fmt(tz, { hour: "2-digit", minute: "2-digit" }).format(date);
}

export type DayGroup<T> = { key: string; date: Date; label: string; rows: T[] };

/** Groups pre-sorted (desc) rows into day buckets, preserving order. */
export function groupByDay<T>(rows: T[], getDate: (row: T) => Date, tz?: string): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const index = new Map<string, DayGroup<T>>();
  for (const row of rows) {
    const date = getDate(row);
    const key = dayKey(date, tz);
    let group = index.get(key);
    if (!group) {
      group = { key, date, label: dayLabel(date, tz), rows: [] };
      index.set(key, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}
