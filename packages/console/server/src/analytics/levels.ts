/**
 * Port of scout's analytics/levels.py: SMA overlays and swing-extrema
 * support/resistance clustering, computed purely from daily bars
 * ({t,o,h,l,c,v}, oldest first). Pure functions, no I/O.
 */

export interface Bar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface Level {
  price: number;
  kind: "support" | "resistance";
  touches: number;
}

export const SMA_WINDOWS = [20, 50, 200] as const;

/** SMA aligned to closes; the first window-1 entries are null (not enough history). */
export function sma(closes: number[], window: number): Array<number | null> {
  const out: Array<number | null> = new Array<number | null>(closes.length).fill(null);
  let running = 0;
  for (let i = 0; i < closes.length; i++) {
    running += closes[i]!;
    if (i >= window) running -= closes[i - window]!;
    if (i >= window - 1) out[i] = running / window;
  }
  return out;
}

export function movingAverages(bars: Bar[]): Record<string, Array<number | null>> {
  const closes = bars.map((b) => b.c);
  const out: Record<string, Array<number | null>> = {};
  for (const w of SMA_WINDOWS) out[`sma${w}`] = sma(closes, w);
  return out;
}

/**
 * A bar is a swing high/low when its high/low is the UNIQUE extremum of the
 * lookback bars on both sides — uniqueness so a flat run doesn't report a
 * swing at every bar of a sideways market.
 */
function swingExtrema(bars: Bar[], lookback: number): { highs: number[]; lows: number[] } {
  const highs = bars.map((b) => b.h);
  const lows = bars.map((b) => b.l);
  const swingHighs: number[] = [];
  const swingLows: number[] = [];
  for (let i = lookback; i < bars.length - lookback; i++) {
    const hWindow = highs.slice(i - lookback, i + lookback + 1);
    const lWindow = lows.slice(i - lookback, i + lookback + 1);
    const h = highs[i]!;
    const l = lows[i]!;
    if (h === Math.max(...hWindow) && hWindow.filter((x) => x === h).length === 1) swingHighs.push(h);
    if (l === Math.min(...lWindow) && lWindow.filter((x) => x === l).length === 1) swingLows.push(l);
  }
  return { highs: swingHighs, lows: swingLows };
}

/** Merge swing prices within tolerance of their neighbor into one (mean, touches) level. */
function cluster(prices: number[], tolerancePct: number): Array<[number, number]> {
  if (prices.length === 0) return [];
  const ordered = [...prices].sort((a, b) => a - b);
  const groups: number[][] = [[ordered[0]!]];
  for (const price of ordered.slice(1)) {
    const lastGroup = groups[groups.length - 1]!;
    const lastPrice = lastGroup[lastGroup.length - 1]!;
    if (Math.abs(price - lastPrice) / lastPrice <= tolerancePct) lastGroup.push(price);
    else groups.push([price]);
  }
  return groups.map((g) => [g.reduce((a, b) => a + b, 0) / g.length, g.length]);
}

export function supportResistance(bars: Bar[], lookback = 3, tolerancePct = 0.005): Level[] {
  const { highs, lows } = swingExtrema(bars, lookback);
  const levels: Level[] = [
    ...cluster(highs, tolerancePct).map(([price, touches]): Level => ({ price, kind: "resistance", touches })),
    ...cluster(lows, tolerancePct).map(([price, touches]): Level => ({ price, kind: "support", touches })),
  ];
  levels.sort((a, b) => a.price - b.price);
  return levels;
}
