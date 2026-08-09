/**
 * Port of scout's analytics/trend.py "price_ma_count" candidate — the
 * best-fitting trend classifier against the hand-collected reference labels
 * (17/25 exact at both horizons; every 1M miss one grade adjacent). Scout's
 * caveat carries over verbatim: a 25-row fit is a hypothesis, not a finding.
 * Pure functions, no I/O; five-grade scale or null when history is short.
 */

export type TrendGrade = "bullish" | "mildly_bullish" | "neutral" | "mildly_bearish" | "bearish";

const GRADES: Record<number, TrendGrade> = {
  4: "bullish",
  3: "mildly_bullish",
  2: "neutral",
  1: "mildly_bearish",
  0: "bearish",
};

function smaLast(closes: number[], period: number): number | null {
  if (period <= 0 || closes.length < period) return null;
  let sum = 0;
  for (let i = closes.length - period; i < closes.length; i++) sum += closes[i]!;
  return sum / period;
}

/** Price vs three SMAs plus one ordering check (fast > slow): 0–4 bullish count → five grades. */
export function priceMaCount(closes: number[], fast: number, mid: number, slow: number): TrendGrade | null {
  const f = smaLast(closes, fast);
  const m = smaLast(closes, mid);
  const s = smaLast(closes, slow);
  if (f === null || m === null || s === null) return null;
  const price = closes[closes.length - 1]!;
  const score =
    Number(price > f) + Number(price > m) + Number(price > s) + Number(f > s);
  return GRADES[score] ?? null;
}

/** Sweep-winner parameters from scout's 2026-08-03 label fit — provisional until re-validated. */
export const PRICE_MA_COUNT_PARAMS = {
  "1m": [20, 26, 30],
  "6m": [15, 21, 50],
} as const;

export interface TrendResult {
  "1m": TrendGrade | null;
  "6m": TrendGrade | null;
}

export function classifyTrend(closes: number[]): TrendResult {
  const p = PRICE_MA_COUNT_PARAMS;
  return {
    "1m": priceMaCount(closes, ...p["1m"]),
    "6m": priceMaCount(closes, ...p["6m"]),
  };
}
