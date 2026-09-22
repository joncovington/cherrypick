/**
 * The opening range (09:30-10:00 ET) as the console shows it: RAW OBSERVATIONS ONLY.
 *
 * Both flies and MEIC open their entry windows at 10:00, so this half-hour is what a reader has in
 * front of them at the moment entries begin. The card exists to show it, not to judge it.
 *
 * Deliberately parameter-free. There is no ATR normalisation, no efficiency ratio and no regime
 * label here, because every one of those has a tunable definition that already lives in
 * `cherrypick.core.openingrange` — and a second definition in TypeScript is how the two quietly
 * drift apart at exactly the edges that matter (a missing bucket, the 10:00 boundary, a DST
 * change). High, low, range and the six bucket closes have one unambiguous meaning each. When the
 * study matures, the classified view arrives by the `regime_cuts` route: the module writes an
 * artifact and the console renders it, deriving nothing.
 *
 * `complete` is the honesty flag: a window missing any of its six buckets is incomplete and its
 * range is NOT a range. It must never render as zero.
 */

export interface OpeningRangeBucket {
  /** Minute-of-day ET at which the bucket starts (570 = 09:30). */
  minute: number;
  open: number;
  high: number;
  low: number;
  close: number;
  ticks: number;
}

export interface OpeningRangePayload {
  session: string | null;
  symbol: string;
  /** All six 5-minute buckets present. When false every measure below is null. */
  complete: boolean;
  /** Why it is not complete: missing buckets, or no trail recorded for the session at all. */
  reason: string | null;
  bucketsPresent: number;
  bucketsExpected: number;
  high: number | null;
  low: number | null;
  /** high - low, in index points. The only derived number here, and it has one definition. */
  rangePoints: number | null;
  /** First tick of the window and the last tick before 10:00. */
  first: number | null;
  last: number | null;
  buckets: OpeningRangeBucket[];
}
