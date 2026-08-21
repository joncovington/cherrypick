/**
 * The statistics every module's performance surface computes the same way.
 *
 * `periodKey` was the reason to make this file: flies and meic each carried a byte-identical copy
 * under different names (`bucketKey` / `periodKey`, arguments swapped). A date-bucketing rule is
 * exactly the kind of duplicate that goes wrong quietly — two copies drifting on where a week
 * starts would group the same sessions differently on two pages, and both would look right.
 *
 * `median` and `stdev` were each in one place only. They live here because a performance surface
 * needs both and there is no second opinion worth having about either.
 */

/**
 * The bucket a trade date belongs to at a given granularity.
 *
 * Weekly buckets are MONDAY-anchored, deliberately: SQLite's `%W` anchors on Sunday and would put
 * a Sunday-to-Monday boundary through the middle of a trading week, splitting a week's sessions
 * across two buckets. Monthly is the ISO prefix; anything else is the day itself.
 */
export function periodKey(granularity: string, tradeDate: string): string {
  if (granularity === "monthly") return tradeDate.slice(0, 7);
  if (granularity === "weekly") {
    const d = new Date(tradeDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  return tradeDate;
}

/** The middle value, averaging the two middles on an even count. `null` for an empty set. */
export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1 ? ordered[mid]! : (ordered[mid - 1]! + ordered[mid]!) / 2;
}

/**
 * Sample standard deviation (n-1). `null` below two values — one observation has no dispersion,
 * and reporting 0 there would be the misleadingly-precise zero this suite's ledgers already refuse
 * to write.
 */
export function stdev(values: number[]): number | null {
  if (values.length < 2) return null;
  const m = values.reduce((s, v) => s + v, 0) / values.length;
  const varr = values.reduce((s, v) => s + (v - m) ** 2, 0) / (values.length - 1);
  return Math.sqrt(varr);
}
