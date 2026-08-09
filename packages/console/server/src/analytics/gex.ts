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

/** (call_wall, put_wall) = strikes of max/min `key` — the net-GEX walls. */
export function netWalls(series: GexStrikeRow[], key: "net_gex" | "net_gex_vol"): [number | null, number | null] {
  if (series.length === 0) return [null, null];
  const call = series.reduce((a, b) => (b[key] > a[key] ? b : a));
  const put = series.reduce((a, b) => (b[key] < a[key] ? b : a));
  return [call.strike, put.strike];
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
    call_iv: number; call_oi: number; call_vol: number; call_gex: number; call_gex_vol: number;
    put_iv: number; put_oi: number; put_vol: number; put_gex: number; put_gex_vol: number;
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

    let gex = dollarGamma(gamma, oiVal, mult, spot);
    let gexVol = dollarGamma(gamma, volVal, mult, spot);
    if (otype.includes("P")) {
      gex = -gex;
      gexVol = -gexVol;
    }

    let d = strikes.get(strike);
    if (d === undefined) {
      d = { call_iv: 0, call_oi: 0, call_vol: 0, call_gex: 0, call_gex_vol: 0, put_iv: 0, put_oi: 0, put_vol: 0, put_gex: 0, put_gex_vol: 0 };
      strikes.set(strike, d);
    }
    if (otype.includes("C")) {
      d.call_iv = Math.round(iv * 100) / 100;
      d.call_oi = oiVal;
      d.call_vol = volVal;
      d.call_gex = gex;
      d.call_gex_vol = gexVol;
    } else if (otype.includes("P")) {
      d.put_iv = Math.round(iv * 100) / 100;
      d.put_oi = oiVal;
      d.put_vol = volVal;
      d.put_gex = gex;
      d.put_gex_vol = gexVol;
    }
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

export function volumeTotals(series: GexStrikeRow[]): {
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
    zero_gamma_vol: interpolateZeroGamma(series, "net_gex_vol"),
    call_wall_vol: callWall,
    put_wall_vol: putWall,
  };
}
