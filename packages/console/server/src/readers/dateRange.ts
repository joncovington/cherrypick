/**
 * A history table's date range (2026-09-25): `?from=YYYY-MM-DD&to=YYYY-MM-DD`, either side open,
 * both inclusive. One parser and one clause builder, so every module's history reads its range the
 * same way the flies trade log always has.
 *
 * Each module filters on the date its history is ABOUT, and its page says which: the trade date for
 * the 0DTE modules (flies, meic), the close for positions held across sessions (bwb, pmcc, curve,
 * earnings) -- a result belongs to the day it was realised -- and the week for calendars.
 */

export interface DateRange {
  from: string | null;
  to: string | null;
}

export const NO_RANGE: DateRange = { from: null, to: null };

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Validated to an ISO date rather than passed through: the bounds go into a comparison against TEXT
 * dates, where a malformed one would silently match nothing and read as "no trades in that range".
 */
export function isoDate(v: unknown): string | null {
  return typeof v === "string" && ISO.test(v) ? v : null;
}

export function parseDateRange(q: unknown): DateRange {
  const query = (q ?? {}) as Record<string, unknown>;
  return { from: isoDate(query["from"]), to: isoDate(query["to"]) };
}

export function hasRange(r: DateRange | undefined): boolean {
  return r !== undefined && (r.from !== null || r.to !== null);
}

/**
 * SQL clauses bounding `column` to the range. `substr(.., 1, 10)` so a timestamp column compares by
 * its date, the same way a date column does.
 */
export function rangeClauses(column: string, r: DateRange | undefined): { clauses: string[]; params: string[] } {
  const clauses: string[] = [];
  const params: string[] = [];
  if (r?.from != null) {
    clauses.push(`substr(${column}, 1, 10) >= ?`);
    params.push(r.from);
  }
  if (r?.to != null) {
    clauses.push(`substr(${column}, 1, 10) <= ?`);
    params.push(r.to);
  }
  return { clauses, params };
}

/** The same bound over a value already in hand. A missing date is outside any set bound. */
export function inRange(date: string | null, r: DateRange | undefined): boolean {
  if (!hasRange(r)) return true;
  if (date === null) return false;
  const d = date.slice(0, 10);
  return (r!.from === null || d >= r!.from) && (r!.to === null || d <= r!.to);
}
