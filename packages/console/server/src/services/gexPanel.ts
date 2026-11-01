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
  atCoverageEdge,
  atSpot,
  expectedMove,
  magnet,
  maxPain,
  oiCoverage,
  panelWalls,
  putCallRatios,
  straddleMove,
  type StrikeOi,
} from "../analytics/gexPanel.js";
import { buildGexProfile, etEpoch, etParts } from "./gexProfile.js";
import path from "node:path";
import { nearestZeroGamma } from "../analytics/gex.js";
import { flowAdjusted, readSignedFlow, type SignedFlow } from "../analytics/gexFlow.js";

/** How many of the strikes the flow moved most the panel lists. */
export const FLOW_TOP_STRIKES = 5;

type FlowRead =
  | { status: "absent" | "empty"; reason: string }
  | { status: "failed"; reason: string }
  | { status: "ok"; flow: SignedFlow };

/** One session's signed flow from gex's `gex_prints`, read-only; says why when there is none. */
function readFlow(config: ConsoleConfig, symbol: string, expiration: string, tradeDate: string): FlowRead {
  const p = path.join(config.paths.gexDir, "gex_history.db");
  if (!fs.existsSync(p)) return { status: "absent", reason: "no gex history database" };
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gex_prints'").get();
    if (has === undefined) return { status: "absent", reason: "the gex recorder has never recorded prints (prints.enabled)" };
    const flow = readSignedFlow(db, symbol, expiration, tradeDate);
    const counted = flow.classified_by.aggressor + flow.classified_by.quote + flow.classified_by.unclassified;
    if (counted === 0 && Object.values(flow.excluded).every((n) => n === 0)) {
      return { status: "empty", reason: `no prints recorded for ${symbol} ${expiration} on ${tradeDate}` };
    }
    return { status: "ok", flow };
  } catch (err) {
    return { status: "failed", reason: (err as Error).message };
  } finally {
    db?.close();
  }
}

/** The panel's flow layer: what the session's prints did to the OI profile. */
function flowLayer(series: GexStrikeRow[], spot: number, read: FlowRead): Record<string, unknown> {
  if (read.status !== "ok") return { status: read.status, reason: read.reason };
  const { flow } = read;
  const adjusted = flowAdjusted(series, flow.strikes, spot);
  const sum = (f: (r: (typeof adjusted)[number]) => number) => adjusted.reduce((s, r) => s + f(r), 0);
  const netBought = (side: "call" | "put") => flow.strikes.filter((s) => s.side === side).reduce((s, r) => s + r.net, 0);
  const counted = flow.classified_by.aggressor + flow.classified_by.quote + flow.classified_by.unclassified;
  const excluded = Object.values(flow.excluded).reduce((s, n) => s + n, 0);
  // The flip of the adjusted profile, by the GEX page's own rule (nearest per-strike sign change).
  const asRows = adjusted.map((r) => ({ ...r, net_gex: r.net_gex_flow }) as unknown as GexStrikeRow);
  return {
    status: "ok",
    contracts: { counted, excluded, classifiedBy: flow.classified_by, excludedBy: flow.excluded },
    netBought: { call: netBought("call"), put: netBought("put") },
    netGexOi: sum((r) => r.net_gex),
    netGexFlow: sum((r) => r.net_gex_flow),
    flowGex: sum((r) => r.flow_gex),
    zeroGammaFlow: nearestZeroGamma(asRows, spot, "net_gex"),
    topStrikes: [...adjusted]
      .filter((r) => r.flow_gex !== 0)
      .sort((a, b) => Math.abs(b.flow_gex) - Math.abs(a.flow_gex))
      .slice(0, FLOW_TOP_STRIKES),
  };
}

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

/**
 * Open interest at every strike of `expiration` the stream cache holds, not just the profile's:
 * the profile drops a leftover strike's OI with its frozen gamma, but OI is a once-a-day count, so
 * a strike the window re-centred away from still has the day's. Only rows stamped on the same ET
 * date as the newest are kept; older ones are another day's count. Null when the cache has none.
 */
function chainOi(config: ConsoleConfig, symbol: string, expiration: string): StrikeOi[] | null {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return null;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const rows = db
      .prepare<[string, string], { data_json: string; open_interest: number | null; updated_at: number | null }>(
        `SELECT ch.data_json, o.open_interest, o.updated_at FROM stream_chain ch
           JOIN stream_oi o ON o.symbol = ch.streamer_symbol
          WHERE ch.underlying_symbol = ? AND ch.expiration = ?`,
      )
      .all(symbol, expiration);
    const stamped = rows.filter((r) => typeof r.updated_at === "number" && typeof r.open_interest === "number");
    if (stamped.length === 0) return null;
    const day = etParts(Math.max(...stamped.map((r) => r.updated_at!)) * 1000).date;
    const byStrike = new Map<number, StrikeOi>();
    for (const r of stamped) {
      if (etParts(r.updated_at! * 1000).date !== day) continue;
      let meta: Record<string, unknown>;
      try {
        meta = JSON.parse(r.data_json) as Record<string, unknown>;
      } catch {
        continue;
      }
      const strike = Number(meta["strike_price"]);
      if (!Number.isFinite(strike)) continue;
      const row = byStrike.get(strike) ?? { strike, call_oi: 0, put_oi: 0 };
      if (String(meta["option_type"] ?? "").toUpperCase().includes("C")) row.call_oi = r.open_interest!;
      else row.put_oi = r.open_interest!;
      byStrike.set(strike, row);
    }
    return [...byStrike.values()].sort((a, b) => a.strike - b.strike);
  } catch {
    return null;
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
  // Off-hours the panel shows the next session's chain, a plan for the open; the last session's,
  // at its close, had every wall on the strikes beside spot and no move left to show.
  const profile = buildGexProfile(config, symbol, nowMs, "next_session");
  if (profile["ok"] !== true) return profile;
  const series = profile["series"] as GexStrikeRow[];
  const spot = profile["spot"] as number;
  const expiration = profile["expiration"] as string;
  const live = profile["sessionMode"] === "live";
  const nextSession = profile["sessionMode"] === "next_session";
  const totals = profile["totals"] as { total_call_gex: number; total_put_gex: number; net_gex: number; zero_gamma: number | null };
  const volTotals = profile["volumeTotals"] as { net_gex_vol: number };
  const oiRows = chainOi(config, symbol, expiration) ?? series;
  const mp = maxPain(oiRows);

  // Time left from the reading to the expiry's 16:00 ET: now in session, the chain's newest greeks
  // off-hours. The last session's fall after its close, so it shows no move rather than a stale
  // one; the next session's IV and the time it is scaled by are then read at the same instant.
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
    netGexVolume: volTotals.net_gex_vol,
    grossGex: totals.total_call_gex + totals.total_put_gex,
    zeroGamma: totals.zero_gamma,
    putCall: putCallRatios(oiRows, series),
    walls: { oi: panelWalls(series, spot, "oi"), volume: panelWalls(series, spot, "volume") },
    maxPain: mp,
    maxPainAtEdge: mp !== null && atCoverageEdge(oiRows, mp),
    oiCoverage: oiCoverage(oiRows),
    atmIv: iv,
    // The straddle at spot, from the strikes either side (`strikes`), and the older quote's time.
    straddle:
      straddle === null
        ? null
        : { mid: straddle, strikes: used.map((s) => s.strike), asOf: Math.min(...used.map((s) => s.asOf)) },
    moves: { straddle: sigma, iv: sigmaIv, ruleOfThumb: ruleOfThumb === null ? null : { ...ruleOfThumb, factor: STRADDLE_BAND } },
    magnet: m === null ? null : { ...m, basis: magnetBasis },
    // The session the chain's prints belong to: the ET date of the reading.
    flow: nextSession
      ? { status: "empty", reason: `the ${expiration} session has not opened` }
      : flowLayer(series, spot, readFlow(config, symbol, expiration, etParts(asOf * 1000).date)),
    spotHistory: profile["spotHistory"],
    spotSession: profile["spotSession"],
  };
}
