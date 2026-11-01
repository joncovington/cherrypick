/**
 * The experimental GEX panel's levels: a reproduction of a third-party 0DTE "walls" panel over the
 * profile `analytics/gex.ts` already computes. Pure functions over the strike series and the chain's
 * quotes. Display only: nothing here is recorded, gates anything or feeds a module, and the walls
 * the recorder and flies' wall-clear arm read are still `netWalls`.
 *
 * The copied panel defines none of its levels, so each is stated here, from the common usage
 * (2026-10-31 research): walls as SpotGamma defines them, the expected move as the 1-sigma move to
 * expiry both off IV and off the straddle, max pain as standard. The magnet has no standard
 * definition; ours is stated on it.
 */

import type { GexStrikeRow } from "./gex.js";

/** Seconds in the calendar year the feed's IV is annualised over. */
export const YEAR_SECONDS = 365 * 86_400;

/**
 * An at-the-money straddle prices the mean absolute move, sqrt(2/pi) = 0.798 of one standard
 * deviation (Black-Scholes, ATM, short horizon), so the straddle-implied 1-sigma move is the
 * straddle over this.
 */
export const STRADDLE_PER_SIGMA = Math.sqrt(2 / Math.PI);

/**
 * The copied panel's band, 0.85 x the ATM straddle: a rule of thumb, about 0.68 sigma
 * (0.85 x 0.798), not the 1-sigma move it is often called. Kept, labelled for what it is.
 */
export const STRADDLE_BAND = 0.85;

/** How many walls each side the panel lists. */
export const WALLS_PER_SIDE = 3;

export interface PanelWall {
  strike: number;
  /** The side's dollar gamma at the strike (call GEX positive, put GEX negative), OI basis. */
  gex: number;
  /** |gex| as a fraction of the side's chain-wide total. */
  share: number;
  /** The side's open interest. */
  oi: number;
  /** The same side's volume today. */
  volume: number;
  /** volume / oi; null when the strike has no open interest. */
  volOi: number | null;
}

/**
 * Up to `WALLS_PER_SIDE` call walls above spot, ranked by CALL gamma (call gamma x call OI), and put
 * walls below it, ranked by PUT gamma: SpotGamma's call and put wall, not the net walls. Ranking on
 * net (call minus put) left a put-heavy chain with no call wall at all (SPX, 2026-10-30). Above and
 * below spot, as the copied panel lists them.
 */
export function panelWalls(series: GexStrikeRow[], spot: number): { call: PanelWall[]; put: PanelWall[] } {
  const totalCall = series.reduce((s, r) => s + Math.max(r.call_gex, 0), 0);
  const totalPut = series.reduce((s, r) => s + Math.max(-r.put_gex, 0), 0);
  const wall = (r: GexStrikeRow, side: "call" | "put"): PanelWall => {
    const oi = side === "call" ? r.call_oi : r.put_oi;
    const volume = side === "call" ? r.call_vol : r.put_vol;
    const gex = side === "call" ? r.call_gex : r.put_gex;
    const total = side === "call" ? totalCall : totalPut;
    return { strike: r.strike, gex, share: total > 0 ? Math.abs(gex) / total : 0, oi, volume, volOi: oi > 0 ? volume / oi : null };
  };
  const call = series
    .filter((r) => r.strike > spot && r.call_gex > 0)
    .sort((a, b) => b.call_gex - a.call_gex)
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "call"));
  const put = series
    .filter((r) => r.strike < spot && r.put_gex < 0)
    .sort((a, b) => a.put_gex - b.put_gex)
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "put"));
  return { call, put };
}

/**
 * Max pain: the strike at which the chain's open interest would pay holders least at expiry
 * (calls `max(0, P - K)`, puts `max(0, K - P)`, each times OI; premiums paid are sunk and ignored).
 * Candidates are the listed strikes; the lower strike wins a tie. Null with no open interest.
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
 * A per-strike value at spot: linear between the two strikes either side of it that have one, the
 * nearest strike's when spot is outside them or only one side has a value. Null with none.
 * Spot rarely sits on a strike, and a straddle at the nearest strike carries its distance from spot
 * as intrinsic value.
 */
export function atSpot(points: Array<{ strike: number; value: number }>, spot: number): number | null {
  const sorted = [...points].sort((a, b) => a.strike - b.strike);
  if (sorted.length === 0) return null;
  const below = sorted.filter((p) => p.strike <= spot).at(-1);
  const above = sorted.find((p) => p.strike >= spot);
  if (below === undefined) return above!.value;
  if (above === undefined) return below.value;
  if (above.strike === below.strike) return below.value;
  const t = (spot - below.strike) / (above.strike - below.strike);
  return below.value + t * (above.value - below.value);
}

/**
 * The 1-sigma move to expiry off IV: spot x IV x sqrt(time left, in years). `iv` is a decimal
 * (0.18). A 0DTE IV is annualised over the hours left, so a whole day's scaling read SPX's Friday
 * close at 72% as a 344-point move. Null at or after expiry.
 */
export function expectedMove(spot: number, iv: number, secondsToExpiry: number): number | null {
  if (!(secondsToExpiry > 0)) return null;
  return spot * iv * Math.sqrt(secondsToExpiry / YEAR_SECONDS);
}

/** The 1-sigma move to expiry off the straddle at spot: the straddle over `STRADDLE_PER_SIGMA`. */
export function straddleMove(straddle: number): number {
  return straddle / STRADDLE_PER_SIGMA;
}

/**
 * The magnet, our definition (there is no standard one): the strike with the most open interest
 * (calls plus puts) between `low` and `high` -- the 1-sigma band -- with its share of the band's
 * OI and the sign of its net GEX. Positive net gamma there is the case said to pin; negative, the
 * case said to push price away. Null when the band holds no OI.
 */
export function magnet(
  series: GexStrikeRow[],
  low: number,
  high: number,
): { strike: number; share: number; oi: number; gammaSign: 1 | -1 | 0 } | null {
  const band = series.filter((r) => r.strike >= low && r.strike <= high && r.call_oi + r.put_oi > 0);
  if (band.length === 0) return null;
  const oiOf = (r: GexStrikeRow) => r.call_oi + r.put_oi;
  const total = band.reduce((s, r) => s + oiOf(r), 0);
  const top = band.reduce((a, b) => (oiOf(b) > oiOf(a) ? b : a));
  return { strike: top.strike, share: oiOf(top) / total, oi: oiOf(top), gammaSign: Math.sign(top.net_gex) as 1 | -1 | 0 };
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
