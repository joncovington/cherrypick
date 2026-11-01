/**
 * The experimental GEX panel's payload: the GEX page's profile (`buildGexProfile`, same source,
 * expiration pick and leftover cut) plus the ATM straddle off the shared stream cache, read-only,
 * and the levels `analytics/gexPanel.ts` derives from them.
 */

import fs from "node:fs";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import type { GexStrikeRow } from "../analytics/gex.js";
import {
  STRADDLE_BAND,
  expectedMove,
  magnet,
  maxPain,
  panelWalls,
  putCallRatios,
} from "../analytics/gexPanel.js";
import { buildGexProfile, etEpoch } from "./gexProfile.js";

interface AtmStraddle {
  strike: number;
  callMid: number;
  putMid: number;
  mid: number;
  /** The older of the two quotes' `updated_at`, epoch seconds. */
  asOf: number;
}

/** The strike nearest spot that has a usable mid on both rights of `expiration`; null otherwise. */
function atmStraddle(
  config: ConsoleConfig,
  symbol: string,
  expiration: string,
  strikes: number[],
  spot: number,
): AtmStraddle | null {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return null;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const bySide = new Map<string, string>(); // `${strike}|C` -> streamer symbol
    const rows = db
      .prepare<[string, string], { streamer_symbol: string; data_json: string }>(
        "SELECT streamer_symbol, data_json FROM stream_chain WHERE underlying_symbol = ? AND expiration = ?",
      )
      .all(symbol, expiration);
    for (const row of rows) {
      try {
        const meta = JSON.parse(row.data_json) as Record<string, unknown>;
        const side = String(meta["option_type"] ?? "").toUpperCase().includes("C") ? "C" : "P";
        bySide.set(`${Number(meta["strike_price"])}|${side}`, row.streamer_symbol);
      } catch {
        continue;
      }
    }
    const quote = db.prepare<[string], { bid: number | null; ask: number | null; mid: number | null; updated_at: number }>(
      "SELECT bid, ask, mid, updated_at FROM stream_quotes WHERE symbol = ?",
    );
    const midOf = (sym: string | undefined): { mid: number; asOf: number } | null => {
      if (sym === undefined) return null;
      const q = quote.get(sym);
      if (q === undefined) return null;
      const mid = q.mid ?? (q.bid !== null && q.ask !== null ? (q.bid + q.ask) / 2 : null);
      return mid !== null && mid > 0 ? { mid, asOf: q.updated_at } : null;
    };
    for (const strike of [...strikes].sort((a, b) => Math.abs(a - spot) - Math.abs(b - spot))) {
      const c = midOf(bySide.get(`${strike}|C`));
      const pq = midOf(bySide.get(`${strike}|P`));
      if (c !== null && pq !== null) {
        return { strike, callMid: c.mid, putMid: pq.mid, mid: c.mid + pq.mid, asOf: Math.min(c.asOf, pq.asOf) };
      }
    }
    return null;
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

/** ATM IV as a decimal: the mean of the call's and put's IV at the strike nearest spot that has both. */
function atmIv(series: GexStrikeRow[], spot: number): { strike: number; iv: number } | null {
  const near = [...series]
    .filter((r) => r.call_iv > 0 && r.put_iv > 0)
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))[0];
  return near === undefined ? null : { strike: near.strike, iv: (near.call_iv + near.put_iv) / 200 };
}

/** The magnet inside the straddle band when there is one, else inside the 1-sigma move; it says which. */
function panelMagnet(
  series: GexStrikeRow[],
  spot: number,
  band: { low: number; high: number } | null,
  em: number | null,
): { strike: number; share: number; basis: "straddle" | "expected_move" } | null {
  const [range, basis] = band !== null ? [band, "straddle" as const] : em !== null ? [{ low: spot - em, high: spot + em }, "expected_move" as const] : [null, null];
  if (range === null || basis === null) return null;
  const m = magnet(series, range.low, range.high);
  return m === null ? null : { ...m, basis };
}

export function buildGexPanel(config: ConsoleConfig, symbol: string, nowMs: number = Date.now()): Record<string, unknown> {
  const profile = buildGexProfile(config, symbol, nowMs);
  if (profile["ok"] !== true) return profile;
  const series = profile["series"] as GexStrikeRow[];
  const spot = profile["spot"] as number;
  const totals = profile["totals"] as { total_call_gex: number; total_put_gex: number; net_gex: number; zero_gamma: number | null };

  const iv = atmIv(series, spot);
  // Time left from the reading to the expiry's 16:00 ET: now in session, the chain's newest greeks
  // off-hours (which fall after the close, so the last session shows no move rather than a stale one).
  const asOf = profile["sessionMode"] === "live" ? nowMs / 1000 : ((profile["chainAsOf"] as number | null) ?? nowMs / 1000);
  const em = iv === null ? null : expectedMove(spot, iv.iv, etEpoch(profile["expiration"] as string, "16:00") - asOf);
  const straddle = atmStraddle(config, symbol, profile["expiration"] as string, series.map((r) => r.strike), spot);
  const band = straddle === null ? null : { low: spot - STRADDLE_BAND * straddle.mid, high: spot + STRADDLE_BAND * straddle.mid };
  return {
    ok: true,
    symbol,
    spot,
    spotUpdatedAt: profile["spotUpdatedAt"],
    expiration: profile["expiration"],
    sessionMode: profile["sessionMode"],
    chainAsOf: profile["chainAsOf"],
    netGex: totals.net_gex,
    grossGex: totals.total_call_gex + totals.total_put_gex,
    zeroGamma: totals.zero_gamma,
    putCall: putCallRatios(series),
    walls: panelWalls(series, spot),
    maxPain: maxPain(series),
    atmIv: iv,
    expectedMove: em === null ? null : { move: em, low: spot - em, high: spot + em },
    straddle: straddle === null ? null : { ...straddle, band: STRADDLE_BAND, low: band!.low, high: band!.high },
    magnet: panelMagnet(series, spot, band, em),
    spotHistory: profile["spotHistory"],
    spotSession: profile["spotSession"],
  };
}
