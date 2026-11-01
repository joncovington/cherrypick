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
 * What a wall's gamma is weighted by: open interest (the broker's once-a-day count, positioning as
 * of the open) or the session's volume (what traded since, the GEX page's flow basis). A 0DTE chain
 * trades many times its open interest (SPX 2026-10-30: the walls 17x to 560x), so the two can
 * disagree.
 */
export type WallBasis = "oi" | "volume";

/**
 * Up to `WALLS_PER_SIDE` call walls above spot, ranked by CALL gamma (call gamma x call OI, or x
 * call volume), and put walls below it, ranked by PUT gamma: SpotGamma's call and put wall, not the
 * net walls. Ranking on net (call minus put) left a put-heavy chain with no call wall at all (SPX,
 * 2026-10-30). Above and below spot, as the copied panel lists them.
 */
export function panelWalls(series: GexStrikeRow[], spot: number, basis: WallBasis = "oi"): { call: PanelWall[]; put: PanelWall[] } {
  const callGex = (r: GexStrikeRow) => (basis === "oi" ? r.call_gex : r.call_gex_vol);
  const putGex = (r: GexStrikeRow) => (basis === "oi" ? r.put_gex : r.put_gex_vol);
  const totalCall = series.reduce((s, r) => s + Math.max(callGex(r), 0), 0);
  const totalPut = series.reduce((s, r) => s + Math.max(-putGex(r), 0), 0);
  const wall = (r: GexStrikeRow, side: "call" | "put"): PanelWall => {
    const oi = side === "call" ? r.call_oi : r.put_oi;
    const volume = side === "call" ? r.call_vol : r.put_vol;
    const gex = side === "call" ? callGex(r) : putGex(r);
    const total = side === "call" ? totalCall : totalPut;
    return { strike: r.strike, gex, share: total > 0 ? Math.abs(gex) / total : 0, oi, volume, volOi: oi > 0 ? volume / oi : null };
  };
  const call = series
    .filter((r) => r.strike > spot && callGex(r) > 0)
    .sort((a, b) => callGex(b) - callGex(a))
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "call"));
  const put = series
    .filter((r) => r.strike < spot && putGex(r) < 0)
    .sort((a, b) => putGex(a) - putGex(b))
    .slice(0, WALLS_PER_SIDE)
    .map((r) => wall(r, "put"));
  return { call, put };
}

/** Open interest at one strike, both rights. */
export type StrikeOi = Pick<GexStrikeRow, "strike" | "call_oi" | "put_oi">;

/**
 * Max pain: the strike at which the chain's open interest would pay holders least at expiry
 * (calls `max(0, P - K)`, puts `max(0, K - P)`, each times OI; premiums paid are sunk and ignored).
 * Candidates are the listed strikes; the lower strike wins a tie. Null with no open interest.
 *
 * Over the strikes the stream covers, never the whole chain: open interest outside them adds a
 * straight line to every candidate's payout (deep calls below pull the answer down, deep puts above
 * push it up), so an answer near the edge of the coverage is not to be trusted (`atCoverageEdge`).
 */
export function maxPain(series: StrikeOi[]): number | null {
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

/** Within this many strikes of either end of the open interest's coverage, max pain is flagged. */
export const COVERAGE_EDGE_STRIKES = 2;

/** The strikes with open interest: lowest, highest, how many. Null with none. */
export function oiCoverage(rows: StrikeOi[]): { low: number; high: number; strikes: number } | null {
  const held = rows.filter((r) => r.call_oi + r.put_oi > 0).map((r) => r.strike);
  if (held.length === 0) return null;
  return { low: Math.min(...held), high: Math.max(...held), strikes: held.length };
}

/** Whether `strike` is within `COVERAGE_EDGE_STRIKES` held strikes of either end of the coverage. */
export function atCoverageEdge(rows: StrikeOi[], strike: number): boolean {
  const held = rows
    .filter((r) => r.call_oi + r.put_oi > 0)
    .map((r) => r.strike)
    .sort((a, b) => a - b);
  const i = held.indexOf(strike);
  return i >= 0 && (i < COVERAGE_EDGE_STRIKES || i >= held.length - COVERAGE_EDGE_STRIKES);
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

/**
 * Put/call ratios: open interest over `oiRows` (every covered strike) and the session's volume over
 * `series` (the live window). Null on a zero call side.
 */
export function putCallRatios(oiRows: StrikeOi[], series: GexStrikeRow[]): { oi: number | null; volume: number | null } {
  const callOi = oiRows.reduce((s, r) => s + r.call_oi, 0);
  const callVol = series.reduce((s, r) => s + r.call_vol, 0);
  return {
    oi: callOi > 0 ? oiRows.reduce((s, r) => s + r.put_oi, 0) / callOi : null,
    volume: callVol > 0 ? series.reduce((s, r) => s + r.put_vol, 0) / callVol : null,
  };
}
