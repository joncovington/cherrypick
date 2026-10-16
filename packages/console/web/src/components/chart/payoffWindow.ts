/**
 * Windowing an expiry-payoff curve to a chosen x range, shared by the flies and MEIC profit
 * forests (moved here from flies' ForestCard, 2026-10-15). A defined-risk payoff is flat beyond
 * its outermost strikes, so a curve is extended flat to the window's edges rather than stopping
 * short, and the y range fits only what is visible inside the window.
 */

/** Extend a payoff curve flat to [xMin, xMax]: beyond its outermost wings it does not change. */
export function extendFlat(xs: number[], ys: number[], xMin: number, xMax: number): { xs: number[]; ys: number[] } {
  const ex = [...xs];
  const ey = [...ys];
  if (ex[0]! > xMin) {
    ex.unshift(xMin);
    ey.unshift(ey[0]!);
  }
  if (ex[ex.length - 1]! < xMax) {
    ex.push(xMax);
    ey.push(ey[ey.length - 1]!);
  }
  return { xs: ex, ys: ey };
}

/** Min/max payoff actually VISIBLE within the x-window; flat boundary value when outside it. */
export function visibleYRange(xs: number[], ys: number[], xMin: number, xMax: number): { min: number; max: number } {
  let mn = Infinity;
  let mx = -Infinity;
  xs.forEach((x, i) => {
    if (x >= xMin && x <= xMax) {
      mn = Math.min(mn, ys[i]!);
      mx = Math.max(mx, ys[i]!);
    }
  });
  if (mn === Infinity) {
    const ext = extendFlat(xs, ys, xMin, xMax);
    mn = mx = ext.ys[0]!;
  }
  return { min: mn, max: mx };
}
