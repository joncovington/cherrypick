/**
 * The experimental GEX panel's payload: the GEX page's profile (`buildGexProfile`, same source,
 * expiration pick and leftover cut) plus the chain's straddles off the shared stream cache,
 * read-only, and the levels `analytics/gexPanel.ts` derives from them. Recomputed on every request,
 * so the moves follow each quote and the clock to expiry.
 */

import fs from "node:fs";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import type { GexStrikeRow } from "../analytics/gex.js";
import {
  STRADDLE_BAND,
  atSpot,
  expectedMove,
  magnet,
  maxPain,
  panelWalls,
  putCallRatios,
  straddleMove,
} from "../analytics/gexPanel.js";
import { buildGexProfile, etEpoch } from "./gexProfile.js";

/**
 * The oldest quote a live straddle may use: `core.streamcache.usable_quote`'s 120 s, the age the gex
 * recorder's quote history and flies price with. Off-hours every quote is old; the page labels the
 * last session instead.
 */
export const QUOTE_MAX_AGE_SECONDS = 120;

/**
 * Inside this long before expiry the 0DTE quotes thin and widen, and a straddle a few cents wide
 * is mostly spread: the moves are flagged, not hidden.
 */
export const LATE_SESSION_SECONDS = 30 * 60;

interface StrikeStraddle {
  strike: number;
  mid: number;
  /** The older of the two quotes' `updated_at`, epoch seconds. */
  asOf: number;
}

/** Every strike of `expiration` with a usable mid on both rights; `minAsOf` drops older quotes. */
function chainStraddles(config: ConsoleConfig, symbol: string, expiration: string, minAsOf: number | null): StrikeStraddle[] {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return [];
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const bySide = new Map<string, string>(); // `${strike}|C` -> streamer symbol
    const strikes = new Set<number>();
    const rows = db
      .prepare<[string, string], { streamer_symbol: string; data_json: string }>(
        "SELECT streamer_symbol, data_json FROM stream_chain WHERE underlying_symbol = ? AND expiration = ?",
      )
      .all(symbol, expiration);
    for (const row of rows) {
      try {
        const meta = JSON.parse(row.data_json) as Record<string, unknown>;
        const strike = Number(meta["strike_price"]);
        if (!Number.isFinite(strike)) continue;
        const side = String(meta["option_type"] ?? "").toUpperCase().includes("C") ? "C" : "P";
        bySide.set(`${strike}|${side}`, row.streamer_symbol);
        strikes.add(strike);
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
      if (q === undefined || (minAsOf !== null && q.updated_at < minAsOf)) return null;
      const mid = q.mid ?? (q.bid !== null && q.ask !== null ? (q.bid + q.ask) / 2 : null);
      return mid !== null && mid > 0 ? { mid, asOf: q.updated_at } : null;
    };
    const out: StrikeStraddle[] = [];
    for (const strike of strikes) {
      const c = midOf(bySide.get(`${strike}|C`));
      const pq = midOf(bySide.get(`${strike}|P`));
      if (c !== null && pq !== null) out.push({ strike, mid: c.mid + pq.mid, asOf: Math.min(c.asOf, pq.asOf) });
    }
    return out;
  } catch {
    return [];
  } finally {
    db?.close();
  }
}

/** The two strikes either side of spot (one when spot is on a strike or past the end), nearest first. */
function bracket<T extends { strike: number }>(rows: T[], spot: number): T[] {
  const sorted = [...rows].sort((a, b) => a.strike - b.strike);
  const below = sorted.filter((r) => r.strike <= spot).at(-1);
  const above = sorted.find((r) => r.strike >= spot);
  return [below, above].filter((r, i, a): r is T => r !== undefined && a.indexOf(r) === i);
}

function band(spot: number, move: number | null): { move: number; low: number; high: number } | null {
  return move === null ? null : { move, low: spot - move, high: spot + move };
}

export function buildGexPanel(config: ConsoleConfig, symbol: string, nowMs: number = Date.now()): Record<string, unknown> {
  const profile = buildGexProfile(config, symbol, nowMs);
  if (profile["ok"] !== true) return profile;
  const series = profile["series"] as GexStrikeRow[];
  const spot = profile["spot"] as number;
  const expiration = profile["expiration"] as string;
  const live = profile["sessionMode"] === "live";
  const totals = profile["totals"] as { total_call_gex: number; total_put_gex: number; net_gex: number; zero_gamma: number | null };

  // Time left from the reading to the expiry's 16:00 ET: now in session, the chain's newest greeks
  // off-hours (which fall after the close, so the last session shows no move rather than a stale one).
  const asOf = live ? nowMs / 1000 : ((profile["chainAsOf"] as number | null) ?? nowMs / 1000);
  const secondsToExpiry = etEpoch(expiration, "16:00") - asOf;

  // ATM IV at spot: the mean of each strike's call and put IV, between the strikes either side.
  const ivPoints = series.filter((r) => r.call_iv > 0 && r.put_iv > 0).map((r) => ({ strike: r.strike, value: (r.call_iv + r.put_iv) / 200 }));
  const iv = atSpot(ivPoints, spot);
  const ivMove = iv === null ? null : expectedMove(spot, iv, secondsToExpiry);

  const straddles = chainStraddles(config, symbol, expiration, live ? nowMs / 1000 - QUOTE_MAX_AGE_SECONDS : null);
  const straddle = secondsToExpiry > 0 ? atSpot(straddles.map((s) => ({ strike: s.strike, value: s.mid })), spot) : null;
  const used = bracket(straddles, spot);
  const sigma = band(spot, straddle === null ? null : straddleMove(straddle));
  const sigmaIv = band(spot, ivMove);
  const ruleOfThumb = band(spot, straddle === null ? null : STRADDLE_BAND * straddle);
  // The magnet's band: the straddle's 1 sigma, else IV's; the payload says which.
  const [magnetBand, magnetBasis] = sigma !== null ? [sigma, "straddle" as const] : sigmaIv !== null ? [sigmaIv, "iv" as const] : [null, null];
  const m = magnetBand === null ? null : magnet(series, magnetBand.low, magnetBand.high);

  return {
    ok: true,
    symbol,
    spot,
    spotUpdatedAt: profile["spotUpdatedAt"],
    expiration,
    sessionMode: profile["sessionMode"],
    chainAsOf: profile["chainAsOf"],
    secondsToExpiry,
    lateSession: secondsToExpiry > 0 && secondsToExpiry < LATE_SESSION_SECONDS,
    netGex: totals.net_gex,
    grossGex: totals.total_call_gex + totals.total_put_gex,
    zeroGamma: totals.zero_gamma,
    putCall: putCallRatios(series),
    walls: panelWalls(series, spot),
    maxPain: maxPain(series),
    atmIv: iv,
    // The straddle at spot, from the strikes either side (`strikes`), and the older quote's time.
    straddle:
      straddle === null
        ? null
        : { mid: straddle, strikes: used.map((s) => s.strike), asOf: Math.min(...used.map((s) => s.asOf)) },
    moves: { straddle: sigma, iv: sigmaIv, ruleOfThumb: ruleOfThumb === null ? null : { ...ruleOfThumb, factor: STRADDLE_BAND } },
    magnet: m === null ? null : { ...m, basis: magnetBasis },
    spotHistory: profile["spotHistory"],
    spotSession: profile["spotSession"],
  };
}
