/**
 * The experimental GEX panel's levels: a reproduction of a third-party 0DTE "walls" panel over the
 * profile `analytics/gex.ts` already computes. Pure functions over the strike series and the ATM
 * quotes. Display only: nothing here is recorded, gates anything or feeds a module, and the walls
 * the recorder and flies' wall-clear arm read are still `netWalls`.
 *
 * Every number has a stated definition here because the panel it copies states none. Where its
 * meaning had to be guessed (the share beside a wall, the magnet), the guess is ours and the page
 * says so.
 */

import type { GexStrikeRow } from "./gex.js";

/** Seconds in the calendar year the feed's IV is annualised over. */
export const YEAR_SECONDS = 365 * 86_400;

/** The straddle fraction the copied panel draws its bands at (0.85 x the ATM straddle). */
export const STRADDLE_BAND = 0.85;

/** How many walls each side the panel lists. */
export const WALLS_PER_SIDE = 3;

export interface PanelWall {
  strike: number;
  /** Net GEX at the strike (OI basis), signed. */
  netGex: number;
  /** |net| as a fraction of the same-signed net summed over the whole chain. */
  share: number;
  /** The wall side's open interest: call OI for a call wall, put OI for a put wall. */
  oi: number;
  /** The same side's volume today. */
  volume: number;
  /** volume / oi; null when the strike has no open interest. */
  volOi: number | null;
}

/**
 * Up to `WALLS_PER_SIDE` call walls above spot (largest positive net first) and put walls below it
 * (most negative first). Above/below spot, as the copied panel lists them ("resistance > spot",
 * "support < spot"); the chain-wide `netWalls` can sit on the other side of spot and these cannot.
 */
export function panelWalls(series: GexStrikeRow[], spot: number): { call: PanelWall[]; put: PanelWall[] } {
  const positive = series.reduce((s, r) => s + (r.net_gex > 0 ? r.net_gex : 0), 0);
  const negative = series.reduce((s, r) => s + (r.net_gex < 0 ? -r.net_gex : 0), 0);
  const wall = (r: GexStrikeRow, side: "call" | "put"): PanelWall => {
    const oi = side === "call" ? r.call_oi : r.put_oi;
    const volume = side === "call" ? r.call_vol : r.put_vol;
    const total = side === "call" ? positive : negative;
    return {
      strike: r.strike,
      netGex: r.net_gex,
      share: total > 0 ? Math.abs(r.net_gex) / total : 0,
      oi,
      volume,
      volOi: oi > 0 ? volume / oi : null,
    };
  };
  const call = series
    .filter((r) => r.strike > spot && r.net_gex > 0)
    .sort((a, b) => b.net_gex - a.net_gex)
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "call"));
  const put = series
    .filter((r) => r.strike < spot && r.net_gex < 0)
    .sort((a, b) => a.net_gex - b.net_gex)
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "put"));
  return { call, put };
}

/**
 * Max pain: the strike at which the chain's open interest would pay holders least at expiry
 * (calls `max(0, P - K)`, puts `max(0, K - P)`, each times OI). Candidates are the listed strikes;
 * the lower strike wins a tie. Null with no open interest.
 */
export function maxPain(series: GexStrikeRow[]): number | null {
  if (!series.some((r) => r.call_oi > 0 || r.put_oi > 0)) return null;
  let best: { strike: number; payout: number } | null = null;
  for (const p of series) {
    let payout = 0;
    for (const r of series) {
      payout += r.call_oi * Math.max(0, p.strike - r.strike) + r.put_oi * Math.max(0, r.strike - p.strike);
    }
    if (best === null || payout < best.payout) best = { strike: p.strike, payout };
  }
  return best!.strike;
}

/**
 * spot x IV x sqrt(time left to expiry, in years): the 1-sigma move to the chain's expiry. `iv` is a
 * decimal (0.18). A 0DTE IV is annualised over the hours left, so scaling it by a whole day (what
 * this did first) read SPX's Friday close at 72% as a 344-point move. Null at or after expiry.
 */
export function expectedMove(spot: number, iv: number, secondsToExpiry: number): number | null {
  if (!(secondsToExpiry > 0)) return null;
  return spot * iv * Math.sqrt(secondsToExpiry / YEAR_SECONDS);
}

/**
 * The magnet: the strike with the largest |net GEX| between `low` and `high` (the straddle band),
 * with its |net| as a share of the band's summed |net|. Our definition: the copied panel prints a
 * strike and a percentage and says nothing of how either is made. Null when the band holds no GEX.
 */
export function magnet(series: GexStrikeRow[], low: number, high: number): { strike: number; share: number } | null {
  const band = series.filter((r) => r.strike >= low && r.strike <= high && r.net_gex !== 0);
  if (band.length === 0) return null;
  const total = band.reduce((s, r) => s + Math.abs(r.net_gex), 0);
  const top = band.reduce((a, b) => (Math.abs(b.net_gex) > Math.abs(a.net_gex) ? b : a));
  return { strike: top.strike, share: Math.abs(top.net_gex) / total };
}

/** Put/call ratios over the chain: open interest and today's volume. Null on a zero call side. */
export function putCallRatios(series: GexStrikeRow[]): { oi: number | null; volume: number | null } {
  const sum = (f: (r: GexStrikeRow) => number) => series.reduce((s, r) => s + f(r), 0);
  const callOi = sum((r) => r.call_oi);
  const callVol = sum((r) => r.call_vol);
  return {
    oi: callOi > 0 ? sum((r) => r.put_oi) / callOi : null,
    volume: callVol > 0 ? sum((r) => r.put_vol) / callVol : null,
  };
}
