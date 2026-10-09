/**
 * Port of cherrypick.core.gex — the suite's one GEX implementation (dollar
 * gamma, cumulative zero-gamma interpolation, per-strike OI + volume series,
 * walls). Pure functions over an already-fetched snapshot.
 */

export interface GexStrikeRow {
  strike: number;
  call_iv: number;
  put_iv: number;
  call_oi: number;
  put_oi: number;
  call_vol: number;
  put_vol: number;
  total_vol: number;
  call_gex: number;
  /** Stored negative so charts draw calls up / puts down. */
  put_gex: number;
  net_gex: number;
  abs_gex: number;
  call_gex_vol: number;
  put_gex_vol: number;
  net_gex_vol: number;
}

export interface GexTotals {
  total_call_gex: number;
  total_put_gex: number;
  net_gex: number;
  gex_positive: boolean;
  max_gex_strike: number | null;
  zero_gamma: number | null;
  call_wall: number | null;
  put_wall: number | null;
}

export function dollarGamma(gamma: number, quantity: number, multiplier: number, spot: number): number {
  return gamma * quantity * multiplier * spot * spot * 0.01;
}

/** Strike where the CUMULATIVE net crosses zero (aggregate dealer flip). */
/**
 * The ONE gamma a strike's call and put both carry: the out-of-the-money side's, else the other's.
 * Mirrors `cherrypick.core.gex.strike_gamma` (the reasoning lives there): netting each side with its
 * own feed gamma let 0DTE quote noise flip a balanced at-the-money strike's sign, and with it the
 * put wall and the nearest zero gamma (SPX 7790, 2026-10-09). At a strike equal to spot, the call.
 */
export function strikeGamma(strike: number, spot: number, callGamma: number, putGamma: number): number {
  const [otm, itm] = strike >= spot ? [callGamma, putGamma] : [putGamma, callGamma];
  return otm ? otm : itm || 0;
}

export function interpolateZeroGamma(strikes: Array<{ strike: number }>, key: string): number | null {
  let cumulative = 0;
  let prevCumulative = 0;
  let prevStrike: number | null = null;
  for (let i = 0; i < strikes.length; i++) {
    const s = strikes[i]! as unknown as Record<string, number>;
    prevCumulative = cumulative;
    cumulative += s[key]!;
    if (i > 0 && ((prevCumulative < 0 && cumulative >= 0) || (prevCumulative >= 0 && cumulative < 0))) {
      const denom = cumulative - prevCumulative;
      const t = denom !== 0 ? -prevCumulative / denom : 0.5;
      return Math.round((prevStrike! + t * (s["strike"]! - prevStrike!)) * 100) / 100;
    }
    prevStrike = s["strike"]!;
  }
  return null;
}

/**
 * Interpolated strike where per-strike `key` changes sign, NEAREST to spot —
 * gexbot's zero-gamma definition: the local flip that governs price here.
 * Deliberately distinct from interpolateZeroGamma (the cumulative whole-book
 * flip); the gex page's panels and overlays use this one.
 */
export function nearestZeroGamma(series: GexStrikeRow[], spot: number, key: "net_gex" | "net_gex_vol"): number | null {
  const crossings: number[] = [];
  for (let i = 0; i < series.length - 1; i++) {
    const a = series[i]!;
    const b = series[i + 1]!;
    const va = a[key];
    const vb = b[key];
    if ((va < 0 && vb >= 0) || (va >= 0 && vb < 0)) {
      const den = vb - va;
      const t = den !== 0 ? -va / den : 0.5;
      crossings.push(Math.round((a.strike + t * (b.strike - a.strike)) * 100) / 100);
    }
  }
  if (crossings.length === 0) return null;
  return crossings.reduce((best, z) => (Math.abs(z - spot) < Math.abs(best - spot) ? z : best));
}

/**
 * (call_wall, put_wall) = strikes of max/min `key` — the net-GEX walls. Mirrors core's `net_walls`:
 * a wall needs a strike on its own side of zero, so a chain with no negative strike has no put wall
 * (an expired 0DTE chain after the bell named its first row, 3000, as the put wall on 2026-10-09).
 */
export function netWalls(series: GexStrikeRow[], key: "net_gex" | "net_gex_vol"): [number | null, number | null] {
  if (series.length === 0) return [null, null];
  const call = series.reduce((a, b) => (b[key] > a[key] ? b : a));
  const put = series.reduce((a, b) => (b[key] < a[key] ? b : a));
  return [call[key] > 0 ? call.strike : null, put[key] < 0 ? put.strike : null];
}

/** How close a runner-up must be to the wall, as a fraction of its net, to be shown beside it. */
export const WALL_NEAR_TIE = 0.8;

export interface WallRunnerUp {
  strike: number;
  /** The runner-up's net as a fraction of the wall's, above WALL_NEAR_TIE and at most 1. */
  strength: number;
}

/**
 * The second strongest strike on each side, when it is within WALL_NEAR_TIE of the wall; else null.
 *
 * A wall is the single most positive (call) or most negative (put) strike, and when two strikes are
 * nearly tied it hops between them on noise: over 229 recorded SPX snapshots, 52 of 53 put-wall
 * hops went to the previous runner-up or came from a near-tie, and on 2026-10-06 the put wall
 * swapped between 7740 and 7600 -- 140 points apart -- about a dozen times. Neither number alone is
 * the read; both are. The wall itself is unchanged (`netWalls`, the recorder's and wall-clear's).
 */
export function contestedWalls(
  series: GexStrikeRow[],
  key: "net_gex" | "net_gex_vol",
): { call: WallRunnerUp | null; put: WallRunnerUp | null } {
  const ranked = [...series].sort((a, b) => b[key] - a[key]);
  const runnerUp = (wall: GexStrikeRow | undefined, next: GexStrikeRow | undefined): WallRunnerUp | null => {
    if (wall === undefined || next === undefined || wall[key] === 0) return null;
    const strength = next[key] / wall[key];
    return strength > WALL_NEAR_TIE ? { strike: next.strike, strength: Math.round(strength * 100) / 100 } : null;
  };
  const top = ranked[0];
  const bottom = ranked[ranked.length - 1];
  return {
    call: top !== undefined && top[key] > 0 ? runnerUp(top, ranked[1]) : null,
    put: bottom !== undefined && bottom[key] < 0 ? runnerUp(bottom, ranked[ranked.length - 2]) : null,
  };
}

export interface ChainEntryInput {
  strikePrice: number;
  streamerSymbol: string;
  optionType: string;
  sharesPerContract: number | null;
}

export function computeGexProfile(
  chainEntries: ChainEntryInput[],
  greeks: Map<string, { gamma: number; iv: number }>,
  oi: Map<string, number>,
  volume: Map<string, number>,
  spot: number,
  defaultMultiplier = 100,
): { ok: true; series: GexStrikeRow[]; totals: GexTotals } | { ok: false; error: string } {
  interface Acc {
    call_gamma: number; call_iv: number; call_oi: number; call_vol: number; call_gex: number; call_gex_vol: number;
    put_gamma: number; put_iv: number; put_oi: number; put_vol: number; put_gex: number; put_gex_vol: number;
    mult: number;
  }
  const strikes = new Map<number, Acc>();

  for (const entry of chainEntries) {
    const strike = entry.strikePrice;
    if (!Number.isFinite(strike) || strike <= 0) continue;
    const otype = entry.optionType.toUpperCase();
    const mult = entry.sharesPerContract ?? defaultMultiplier;
    const oiVal = Math.trunc(oi.get(entry.streamerSymbol) ?? 0);
    const volVal = Math.trunc(volume.get(entry.streamerSymbol) ?? 0);
    const g = greeks.get(entry.streamerSymbol);
    const gamma = g?.gamma ?? 0;
    const iv = g?.iv ?? 0;

    let d = strikes.get(strike);
    if (d === undefined) {
      d = {
        call_gamma: 0, call_iv: 0, call_oi: 0, call_vol: 0, call_gex: 0, call_gex_vol: 0,
        put_gamma: 0, put_iv: 0, put_oi: 0, put_vol: 0, put_gex: 0, put_gex_vol: 0, mult,
      };
      strikes.set(strike, d);
    }
    if (otype.includes("C")) {
      d.call_gamma = gamma;
      d.call_iv = Math.round(iv * 100) / 100;
      d.call_oi = oiVal;
      d.call_vol = volVal;
    } else if (otype.includes("P")) {
      d.put_gamma = gamma;
      d.put_iv = Math.round(iv * 100) / 100;
      d.put_oi = oiVal;
      d.put_vol = volVal;
    }
  }

  // One gamma per strike, the out-of-the-money side's (`strikeGamma`, mirroring core.gex.strike_gamma).
  for (const [strike, d] of strikes) {
    const gamma = strikeGamma(strike, spot, d.call_gamma, d.put_gamma);
    d.call_gex = dollarGamma(gamma, d.call_oi, d.mult, spot);
    d.call_gex_vol = dollarGamma(gamma, d.call_vol, d.mult, spot);
    d.put_gex = -dollarGamma(gamma, d.put_oi, d.mult, spot);
    d.put_gex_vol = -dollarGamma(gamma, d.put_vol, d.mult, spot);
  }

  if (strikes.size === 0) {
    return { ok: false, error: "insufficient GEX data — OI/volume not yet cached (streamer must run first)" };
  }

  const series: GexStrikeRow[] = [...strikes.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([strike, d]) => {
      const net = d.call_gex + d.put_gex;
      const netVol = d.call_gex_vol + d.put_gex_vol;
      return {
        strike,
        call_iv: d.call_iv,
        put_iv: d.put_iv,
        call_oi: d.call_oi,
        put_oi: d.put_oi,
        call_vol: d.call_vol,
        put_vol: d.put_vol,
        total_vol: d.call_vol + d.put_vol,
        call_gex: Math.round(d.call_gex),
        put_gex: Math.round(d.put_gex),
        net_gex: Math.round(net),
        abs_gex: Math.round(Math.abs(net)),
        call_gex_vol: Math.round(d.call_gex_vol),
        put_gex_vol: Math.round(d.put_gex_vol),
        net_gex_vol: Math.round(netVol),
      };
    });

  const totalCall = series.reduce((s, r) => s + (r.call_gex > 0 ? r.call_gex : 0), 0);
  const totalPut = Math.abs(series.reduce((s, r) => s + (r.put_gex < 0 ? r.put_gex : 0), 0));
  const netTotal = series.reduce((s, r) => s + r.net_gex, 0);
  const maxAbs = series.reduce((a, b) => (b.abs_gex > a.abs_gex ? b : a));
  const callWall = series.reduce((a, b) => (b.call_gex > a.call_gex ? b : a));
  const putWall = series.reduce((a, b) => (b.put_gex < a.put_gex ? b : a));

  return {
    ok: true,
    series,
    totals: {
      total_call_gex: Math.round(totalCall),
      total_put_gex: Math.round(totalPut),
      net_gex: Math.round(netTotal),
      gex_positive: netTotal > 0,
      max_gex_strike: maxAbs.strike,
      zero_gamma: interpolateZeroGamma(series, "net_gex"),
      call_wall: callWall.strike,
      put_wall: putWall.strike,
    },
  };
}

/**
 * Volume-basis roll-ups matching the gex service exactly: zero gamma is
 * nearest_zero_gamma (needs spot), walls are the net walls.
 */
export function volumeTotals(
  series: GexStrikeRow[],
  spot: number,
): {
  total_call_gex_vol: number;
  total_put_gex_vol: number;
  net_gex_vol: number;
  zero_gamma_vol: number | null;
  call_wall_vol: number | null;
  put_wall_vol: number | null;
} {
  const totalCall = series.reduce((s, r) => s + (r.call_gex_vol > 0 ? r.call_gex_vol : 0), 0);
  const totalPut = Math.abs(series.reduce((s, r) => s + (r.put_gex_vol < 0 ? r.put_gex_vol : 0), 0));
  const net = series.reduce((s, r) => s + r.net_gex_vol, 0);
  const [callWall, putWall] = netWalls(series, "net_gex_vol");
  return {
    total_call_gex_vol: Math.round(totalCall),
    total_put_gex_vol: Math.round(totalPut),
    net_gex_vol: Math.round(net),
    zero_gamma_vol: nearestZeroGamma(series, spot, "net_gex_vol"),
    call_wall_vol: callWall,
    put_wall_vol: putWall,
  };
}
