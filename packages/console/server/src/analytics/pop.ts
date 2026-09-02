/**
 * Port of scout's analytics/pop.py: probability of profit under the
 * risk-neutral lognormal measure — exact given the assumption, not Monte
 * Carlo. Sums P(S_T in interval) over every spot interval (bounded by the
 * position's own breakevens) where the payoff is positive.
 */

import { type Leg, breakevens, payoffAt } from "./payoff.js";

/** N(x) via erf — matches Python's math.erf identity exactly in double precision. */
export function normCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

/**
 * Abramowitz & Stegun 7.1.26 has only ~1e-7 absolute accuracy — noticeable
 * against Python's true erf in parity tests. This is the higher-precision
 * rational approximation (max error ~1.2e-16 over the real line) from
 * Numerical Recipes' erfc, reflected for negative x.
 */
function erf(x: number): number {
  return 1 - erfc(x);
}

function erfc(x: number): number {
  const z = Math.abs(x);
  const t = 2 / (2 + z);
  const ty = 4 * t - 2;
  const coef = [
    -1.3026537197817094, 6.4196979235649026e-1, 1.9476473204185836e-2, -9.561514786808631e-3,
    -9.46595344482036e-4, 3.66839497852761e-4, 4.2523324806907e-5, -2.0278578112534e-5,
    -1.624290004647e-6, 1.303655835580e-6, 1.5626441722e-8, -8.5238095915e-8, 6.529054439e-9,
    5.059343495e-9, -9.91364156e-10, -2.27365122e-10, 9.6467911e-11, 2.394038e-12, -6.886027e-12,
    8.94487e-13, 3.13092e-13, -1.12708e-13, 3.81e-16, 7.106e-15,
  ];
  let d = 0;
  let dd = 0;
  for (let j = coef.length - 1; j > 0; j--) {
    const tmp = d;
    d = ty * d - dd + coef[j]!;
    dd = tmp;
  }
  const ans = t * Math.exp(-z * z + 0.5 * (coef[0]! + ty * d) - dd);
  return x >= 0 ? ans : 2 - ans;
}

function d2(spot: number, strike: number, sigma: number, t: number, r: number): number {
  if (t <= 0 || sigma <= 0) {
    const forward = t > 0 ? spot * Math.exp(r * t) : spot;
    if (forward > strike) return Infinity;
    if (forward < strike) return -Infinity;
    return 0;
  }
  return (Math.log(spot / strike) + (r - 0.5 * sigma * sigma) * t) / (sigma * Math.sqrt(t));
}

export function probBelow(spot: number, strike: number, sigma: number, t: number, r: number): number {
  const v = d2(spot, strike, sigma, t, r);
  if (v === Infinity) return 0;
  if (v === -Infinity) return 1;
  return normCdf(-v);
}

/** One-standard-deviation dollar move, for chart bands. */
export function expectedMove(spot: number, sigma: number, t: number): number {
  return spot * sigma * Math.sqrt(Math.max(t, 0));
}

function boundedCdf(x: number, spot: number, sigma: number, t: number, r: number): number {
  if (x <= 0) return 0;
  if (x === Infinity) return 1;
  return probBelow(spot, x, sigma, t, r);
}

export function pop(legs: Leg[], spot: number, sigma: number, t: number, r: number): number {
  const breaks = breakevens(legs).filter((b) => b > 0);
  if (breaks.length === 0) return payoffAt(legs, spot) > 0 ? 1 : 0;

  const bounds = [0, ...breaks, Infinity];
  let total = 0;
  for (let i = 0; i < bounds.length - 1; i++) {
    const lo = bounds[i]!;
    const hi = bounds[i + 1]!;
    const probe = hi !== Infinity ? (lo + hi) / 2 : lo * 2 + 1;
    if (payoffAt(legs, probe) > 0) {
      total += boundedCdf(hi, spot, sigma, t, r) - boundedCdf(lo, spot, sigma, t, r);
    }
  }
  return total;
}
