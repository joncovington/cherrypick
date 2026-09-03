/**
 * Axis-tick helpers shared by the hand-SVG chart family -- extracted 2026-09 from byte-identical
 * copies (`ticksFor` in ForestCard.tsx, MeicForestCard.tsx and TimelineCard.tsx were the same
 * function under the same name; `Charts.tsx`'s `niceTicks` was a fourth). One export, same name
 * `Charts.tsx` already used, so nothing there had to change.
 */

/** "Nice" tick values spanning [min, max], roughly `target` of them. General-purpose numeric
 *  axis -- dollars, deltas, whatever the chart is plotting. */
export function niceTicks(min: number, max: number, target: number): number[] {
  const span = max - min || 1;
  const raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((s) => span / s <= target + 1) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

/**
 * Tick values in MINUTES for a time-of-day axis, stepping through a fixed set of round
 * intervals (5/10/15/30/60/120 min) rather than `niceTicks`' decade-based steps -- extracted
 * from `Flies/JournalCard.tsx`'s own `timeTicks`, deliberately kept distinct from `niceTicks`
 * rather than merged into it: a clock axis and a dollar axis round differently on purpose.
 */
export function timeTicks(min: number, max: number, target: number): number[] {
  const span = max - min || 1;
  const steps = [5, 10, 15, 30, 60, 120];
  const step = steps.find((s) => span / s <= target) ?? 120;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v);
  return out;
}
