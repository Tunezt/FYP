/** Day grouping for chronological lists — the day is the unit of history.
 *
 * All bucketing/labels are computed in the BUSINESS timezone (not the
 * browser's): the backend's "today" numbers use the business tz, and a sale at
 * 23:30 WIB must not jump to "besok" for an owner whose laptop is set to
 * Malaysia time. Pass `business.timezone`; falls back to the browser tz.
 *
 * They are also computed on the business's DAY BOUNDARY (M15-T4). A café that
 * closes at 23:30 settles bills after midnight; with `day_start_hour = 4` the
 * business day runs 04:00 → 04:00, so a bill at 00:15 belongs under the night
 * before — the same day the backend's metric layer reports it under. Pass
 * `business.day_start_hour`; 0 is the plain calendar day.
 *
 * Shifting an instant back by `dayStartHour` maps each business day onto the
 * calendar day it starts on, so one subtraction does the whole job. Indonesia
 * and Malaysia have no DST, which is what makes a fixed offset exact here. */

function fmt(tz: string | undefined, opts: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("id-ID", { ...opts, ...(tz ? { timeZone: tz } : {}) });
}

/** The instant whose calendar day, in `tz`, is this instant's BUSINESS day. */
function onBusinessDay(d: Date, dayStartHour = 0): Date {
  return dayStartHour ? new Date(d.getTime() - dayStartHour * 3_600_000) : d;
}

/** YYYY-MM-DD of the business day (en-CA gives ISO-like ordering). */
function dayKey(d: Date, tz?: string, dayStartHour = 0): string {
  return new Intl.DateTimeFormat("en-CA", {
    ...(tz ? { timeZone: tz } : {}),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(onBusinessDay(d, dayStartHour));
}

export function dayLabel(date: Date, tz?: string, dayStartHour = 0, now = new Date()): string {
  const key = dayKey(date, tz, dayStartHour);
  const todayKey = dayKey(now, tz, dayStartHour);
  if (key === todayKey) return "Hari ini";
  if (key === dayKey(new Date(now.getTime() - 86_400_000), tz, dayStartHour)) return "Kemarin";
  const sameYear = key.slice(0, 4) === todayKey.slice(0, 4);
  const on = onBusinessDay(date, dayStartHour);
  return fmt(tz, sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" }).format(on);
}

export function daySubLabel(date: Date, tz?: string, dayStartHour = 0, now = new Date()): string | null {
  // Weekday under a relative label; nothing when the label is already a date.
  const key = dayKey(date, tz, dayStartHour);
  const isToday = key === dayKey(now, tz, dayStartHour);
  const isYesterday = key === dayKey(new Date(now.getTime() - 86_400_000), tz, dayStartHour);
  if (isToday || isYesterday) return fmt(tz, { weekday: "long" }).format(onBusinessDay(date, dayStartHour));
  return null;
}

/** The wall-clock time, which is never shifted: a bill rung up at 00:15 shows
 *  00:15, under the previous day's header. */
export function timeLabel(date: Date, tz?: string): string {
  return fmt(tz, { hour: "2-digit", minute: "2-digit" }).format(date);
}

export type DayGroup<T> = { key: string; date: Date; label: string; rows: T[] };

/** Groups pre-sorted (desc) rows into business-day buckets, preserving order. */
export function groupByDay<T>(
  rows: T[],
  getDate: (row: T) => Date,
  tz?: string,
  dayStartHour = 0,
): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const index = new Map<string, DayGroup<T>>();
  for (const row of rows) {
    const date = getDate(row);
    const key = dayKey(date, tz, dayStartHour);
    let group = index.get(key);
    if (!group) {
      group = { key, date, label: dayLabel(date, tz, dayStartHour), rows: [] };
      index.set(key, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}
