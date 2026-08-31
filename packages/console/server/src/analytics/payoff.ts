/**
 * Option payoff arithmetic — pure, no imports, no I/O.
 *
 * **NOT research-only, despite the neighbourhood.** `readers/meic.ts` uses `payoffAt` for the
 * profit-forest curves, so this file survived the 2026-08-31 retirement of the research/screener
 * section while its other consumers (the builder, the screener, `/api/payoff`) did not. Deleting it
 * as part of that cull would have silently emptied MEIC's forest — the page would still render.
 * `test/payoff-survives.test.ts` pins the dependency.
 */
/**
 * Port of scout's analytics/payoff.py: generic leg-list → payoff engine.
 * A Leg is priced per contract (1 contract = 100 shares); quantity is signed
 * (positive long, negative short); price is per share paid to open. An option
 * payoff is exactly piecewise-linear with kinks only at strikes, so the curve
 * is evaluated at the strikes themselves — exact, not an approximation — and
 * breakevens/extrema follow from those points plus the two analytic tail slopes.
 */

export interface Leg {
  kind: "call" | "put" | "stock";
  quantity: number;
  price: number;
  strike?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
}

const GREEK_FIELDS = ["delta", "gamma", "theta", "vega"] as const;

function intrinsic(leg: Leg, spot: number): number {
  if (leg.kind === "call") return Math.max(0, spot - (leg.strike ?? 0));
  if (leg.kind === "put") return Math.max(0, (leg.strike ?? 0) - spot);
  return spot; // stock
}

export function payoffAt(legs: Leg[], spot: number): number {
  return legs.reduce((sum, leg) => sum + (intrinsic(leg, spot) - leg.price) * leg.quantity * 100, 0);
}

function kinks(legs: Leg[]): number[] {
  return [...new Set(legs.map((l) => l.strike).filter((s): s is number => s != null))].sort(
    (a, b) => a - b,
  );
}

export function payoffCurve(legs: Leg[]): Array<{ spot: number; pnl: number }> {
  return kinks(legs).map((k) => ({ spot: k, pnl: payoffAt(legs, k) }));
}

/** d(pnl)/d(spot) below every strike: puts ITM (−1/share), calls worthless, stock full delta. */
export function slopeBelow(legs: Leg[]): number {
  let slope = 0;
  for (const leg of legs) {
    if (leg.kind === "put") slope -= leg.quantity * 100;
    else if (leg.kind === "stock") slope += leg.quantity * 100;
  }
  return slope;
}

/** d(pnl)/d(spot) above every strike: calls ITM (+1/share), puts worthless. */
export function slopeAbove(legs: Leg[]): number {
  let slope = 0;
  for (const leg of legs) {
    if (leg.kind === "call") slope += leg.quantity * 100;
    else if (leg.kind === "stock") slope += leg.quantity * 100;
  }
  return slope;
}

export function breakevens(legs: Leg[]): number[] {
  const ks = kinks(legs);
  if (ks.length === 0) return [];
  const points = ks.map((k) => [k, payoffAt(legs, k)] as const);
  const crossings: number[] = [];

  for (let i = 0; i < points.length - 1; i++) {
    const [x0, y0] = points[i]!;
    const [x1, y1] = points[i + 1]!;
    if (y0 === 0) crossings.push(x0);
    else if (y0 * y1 < 0) crossings.push(x0 + ((0 - y0) * (x1 - x0)) / (y1 - y0));
  }
  const [lastX, lastY] = points[points.length - 1]!;
  if (lastY === 0) crossings.push(lastX);

  const [x0, y0] = points[0]!;
  const below = slopeBelow(legs);
  if (below !== 0) {
    const xCross = x0 - y0 / below;
    if (xCross <= x0) crossings.push(xCross);
  }
  const above = slopeAbove(legs);
  if (above !== 0) {
    const xCross = lastX - lastY / above;
    if (xCross >= lastX) crossings.push(xCross);
  }

  return [...new Set(crossings.map((c) => Math.round(c * 1e6) / 1e6))].sort((a, b) => a - b);
}

export interface Extremum {
  value: number | null;
  unbounded: boolean;
}

export function maxProfit(legs: Leg[]): Extremum {
  if (slopeAbove(legs) > 0) return { value: null, unbounded: true };
  const ks = kinks(legs);
  const candidates = ks.length > 0 ? ks.map((k) => payoffAt(legs, k)) : [payoffAt(legs, 0)];
  candidates.push(payoffAt(legs, 0));
  return { value: Math.max(...candidates), unbounded: false };
}

export function maxLoss(legs: Leg[]): Extremum {
  if (slopeAbove(legs) < 0) return { value: null, unbounded: true };
  const ks = kinks(legs);
  const candidates = ks.length > 0 ? ks.map((k) => payoffAt(legs, k)) : [payoffAt(legs, 0)];
  candidates.push(payoffAt(legs, 0));
  return { value: Math.min(...candidates), unbounded: false };
}

/** Quantity-weighted greek rollup; stock has implicit delta 1; null only when no leg supplied it. */
export function netGreeks(legs: Leg[]): Record<(typeof GREEK_FIELDS)[number], number | null> {
  const totals: Record<string, number> = { delta: 0, gamma: 0, theta: 0, vega: 0 };
  const present: Record<string, boolean> = { delta: false, gamma: false, theta: false, vega: false };
  for (const leg of legs) {
    if (leg.kind === "stock") {
      totals["delta"] = totals["delta"]! + 1 * leg.quantity * 100;
      present["delta"] = true;
      continue;
    }
    for (const g of GREEK_FIELDS) {
      const v = leg[g];
      if (v != null) {
        totals[g] = totals[g]! + v * leg.quantity * 100;
        present[g] = true;
      }
    }
  }
  return {
    delta: present["delta"] ? totals["delta"]! : null,
    gamma: present["gamma"] ? totals["gamma"]! : null,
    theta: present["theta"] ? totals["theta"]! : null,
    vega: present["vega"] ? totals["vega"]! : null,
  };
}
