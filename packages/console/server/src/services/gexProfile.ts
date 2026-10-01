/**
 * GEX profile assembled entirely from the shared stream cache read-only —
 * same source and expiration-pick discipline as the gex module's provider
 * in session (the nearest expiration that actually has live greeks), the
 * last session's chain off-hours (labelled), and the same leftover-strike
 * cut either way.
 */

import fs from "node:fs";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import path from "node:path";
import { computeGexProfile, volumeTotals, nearestZeroGamma, netWalls, type ChainEntryInput } from "../analytics/gex.js";

/** Epoch seconds of an ET wall-clock time on a date, DST-aware via Intl. */
function etEpoch(date: string, time: string): number {
  const probe = new Date(`${date}T12:00:00Z`);
  const offsetPart = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    timeZoneName: "longOffset",
  })
    .formatToParts(probe)
    .find((p) => p.type === "timeZoneName")?.value; // e.g. "GMT-04:00"
  const offset = offsetPart?.replace("GMT", "") ?? "-05:00";
  return Date.parse(`${date}T${time}:00${offset}`) / 1000;
}

/** Today's intraday spot trail from the gex module's own history DB (read-only). */
function spotHistory(config: ConsoleConfig, symbol: string): Array<{ ts: number; spot: number }> {
  const p = path.join(config.paths.gexDir, "gex_history.db");
  if (!fs.existsSync(p)) return [];
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    // The recorder also runs off-hours, writing the frozen cached spot — a
    // "trail" of identical points. Use the most recent date with real
    // movement (a genuine session), not just the most recent date.
    return db
      .prepare<[string, string], Record<string, unknown>>(
        `SELECT ts, spot FROM gex_spot_history
          WHERE symbol = ? AND trade_date = (
            SELECT trade_date FROM gex_spot_history WHERE symbol = ?
             GROUP BY trade_date HAVING COUNT(DISTINCT spot) > 1
             ORDER BY trade_date DESC LIMIT 1)
          ORDER BY ts`,
      )
      .all(symbol, symbol)
      .map((r) => ({ ts: Number(r["ts"]), spot: Number(r["spot"]) }))
      .filter((r) => Number.isFinite(r.ts) && Number.isFinite(r.spot));
  } catch {
    return [];
  } finally {
    db?.close();
  }
}

/**
 * A strike whose greeks stopped updating this long before its chain's newest row is a leftover:
 * the producer re-centred its window away and nothing deletes the row, so its gamma is frozen at
 * whatever it was when spot was nearby. Same rule and number as the gex provider's
 * `LEFTOVER_ROW_SECONDS` -- on 2026-09-30 a week of leftovers on the 10-02 chain moved its
 * zero-gamma from 7,420 to 6,875.
 */
export const LEFTOVER_ROW_SECONDS = 600;

/** ET calendar date, minutes since midnight and weekday (0 = Sunday) of an instant. */
export function etParts(nowMs: number): { date: string; minutes: number; weekday: number } {
  const d = new Date(nowMs);
  const date = d.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const [h, m] = d
    .toLocaleTimeString("en-GB", { timeZone: "America/New_York", hour12: false })
    .split(":")
    .map(Number);
  const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
  return { date, minutes: (h! % 24) * 60 + m!, weekday };
}

/**
 * Which expirations may back the profile now, in preference order, and what they mean.
 *
 * In session (a weekday, 09:30-16:00 ET): the nearest expiration on or after today -- the session's
 * chain, or the next one when today's is not live, as the recorder does. Off-hours there is no
 * session to describe, and what used to fill the gap was whatever chain was still streaming: usually
 * another module's extra window, a different expiry. Off-hours therefore shows the LAST session's
 * chain, labelled -- today's after the bell, otherwise the latest one before today. Never a later
 * expiry. No NYSE holiday calendar here; on a holiday the expiry chip still names what is shown.
 */
export function candidateExpirations(
  expirations: string[],
  nowMs: number,
): { mode: "live" | "last_session"; order: string[] } {
  const { date, minutes, weekday } = etParts(nowMs);
  const weekdayNow = weekday >= 1 && weekday <= 5;
  if (weekdayNow && minutes >= 9 * 60 + 30 && minutes < 16 * 60) {
    return { mode: "live", order: expirations.filter((e) => e >= date) };
  }
  const past = expirations.filter((e) => e < date).reverse();
  const afterBell = weekdayNow && minutes >= 16 * 60 && expirations.includes(date);
  return { mode: "last_session", order: afterBell ? [date, ...past] : past };
}

/** The streamer symbols to drop as leftovers, given each strike's greeks `updated_at`. */
export function leftoverSymbols(stamps: Map<string, number>): Set<string> {
  let newest = -Infinity;
  for (const ts of stamps.values()) newest = Math.max(newest, ts);
  const cutoff = newest - LEFTOVER_ROW_SECONDS;
  return new Set([...stamps].filter(([, ts]) => ts < cutoff).map(([sym]) => sym));
}

export function buildGexProfile(
  config: ConsoleConfig,
  symbol: string,
  nowMs: number = Date.now(),
): Record<string, unknown> {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return { ok: false, error: "stream cache missing" };
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");

    const trade = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT last, volume, updated_at FROM stream_trades WHERE symbol = ?",
      )
      .get(symbol);
    const spot = typeof trade?.["last"] === "number" ? trade["last"] : null;
    // Seconds-epoch, same as every other reader of this table (meic/flies/earnings providers).
    const spotUpdatedAt = typeof trade?.["updated_at"] === "number" ? trade["updated_at"] : null;
    if (spot === null) return { ok: false, error: `no cached spot for ${symbol}` };

    // Nearest expiration with greeks coverage.
    const expirations = db
      .prepare<[string], { expiration: string }>(
        "SELECT DISTINCT expiration FROM stream_chain WHERE underlying_symbol = ? ORDER BY expiration",
      )
      .all(symbol)
      .map((r) => r.expiration);
    const greeksStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT gamma, iv, updated_at FROM stream_greeks WHERE symbol = ?",
    );
    const oiStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT open_interest FROM stream_oi WHERE symbol = ?",
    );
    const volStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT volume FROM stream_trades WHERE symbol = ?",
    );

    // "Today" is the ET date: this read `toISOString()`, the UTC date, which turns over at 20:00 ET.
    const { mode, order } = candidateExpirations(expirations, nowMs);
    for (const expiration of order) {
      const rows = db
        .prepare<[string, string], { streamer_symbol: string; data_json: string }>(
          "SELECT streamer_symbol, data_json FROM stream_chain WHERE underlying_symbol = ? AND expiration = ?",
        )
        .all(symbol, expiration);

      const entries: ChainEntryInput[] = [];
      const greeks = new Map<string, { gamma: number; iv: number }>();
      const stamps = new Map<string, number>();
      const oi = new Map<string, number>();
      const volume = new Map<string, number>();
      for (const row of rows) {
        let meta: Record<string, unknown>;
        try {
          meta = JSON.parse(row.data_json) as Record<string, unknown>;
        } catch {
          continue;
        }
        entries.push({
          strikePrice: Number(meta["strike_price"]),
          streamerSymbol: row.streamer_symbol,
          optionType: String(meta["option_type"] ?? ""),
          sharesPerContract: typeof meta["shares_per_contract"] === "number" ? meta["shares_per_contract"] : null,
        });
        const g = greeksStmt.get(row.streamer_symbol);
        if (typeof g?.["gamma"] === "number") {
          // stream_greeks.iv is a decimal; the profile displays percent.
          greeks.set(row.streamer_symbol, {
            gamma: g["gamma"],
            iv: typeof g["iv"] === "number" ? g["iv"] * 100 : 0,
          });
          if (typeof g["updated_at"] === "number") stamps.set(row.streamer_symbol, g["updated_at"]);
        }
        const o = oiStmt.get(row.streamer_symbol);
        if (typeof o?.["open_interest"] === "number") oi.set(row.streamer_symbol, o["open_interest"]);
        const v = volStmt.get(row.streamer_symbol);
        if (typeof v?.["volume"] === "number") volume.set(row.streamer_symbol, v["volume"]);
      }
      if (greeks.size === 0) continue; // no live greeks on this expiration — try the next

      const leftovers = leftoverSymbols(stamps);
      for (const sym of leftovers) {
        greeks.delete(sym);
        oi.delete(sym);
        stamps.delete(sym);
      }
      const chainAsOf = stamps.size > 0 ? Math.max(...stamps.values()) : null;

      const profile = computeGexProfile(entries, greeks, oi, volume, spot);
      if (!profile.ok) continue;
      // The gex service overrides the profile's cumulative flip and gamma-pile
      // walls: panels/overlays use nearest_zero_gamma and the net walls.
      profile.totals.zero_gamma = nearestZeroGamma(profile.series, spot, "net_gex");
      [profile.totals.call_wall, profile.totals.put_wall] = netWalls(profile.series, "net_gex");
      let trail = spotHistory(config, symbol);
      let spotSession: { date: string; openTs: number; closeTs: number } | null = null;
      if (trail.length > 0) {
        // The recorder also logs overnight/pre-market ticks; the trail shows
        // the regular session only, 9:30–16:00 ET on the recorded date.
        const date = new Date(trail[trail.length - 1]!.ts * 1000).toLocaleDateString("en-CA", {
          timeZone: "America/New_York",
        });
        spotSession = { date, openTs: etEpoch(date, "09:30"), closeTs: etEpoch(date, "16:00") };
        const { openTs, closeTs } = spotSession;
        trail = trail.filter((t) => t.ts >= openTs && t.ts <= closeTs);
      }
      return {
        ok: true,
        symbol,
        spot,
        spotUpdatedAt,
        expiration,
        // "live" in session; "last_session" off-hours, when the chart is the last session's chain
        // as of `chainAsOf` (its newest greeks) and must say so rather than read as now.
        sessionMode: mode,
        chainAsOf,
        leftoverRowsDropped: leftovers.size,
        series: profile.series,
        totals: profile.totals,
        volumeTotals: volumeTotals(profile.series, spot),
        spotHistory: trail,
        spotSession,
      };
    }
    return { ok: false, error: `no expiration with cached greeks for ${symbol}` };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  } finally {
    db?.close();
  }
}

/**
 * The symbols the GEX recorder is STILL writing, derived from its latest session.
 *
 * One definition, imported by `readers/gex.ts` for its staleness roster too: "which symbols does
 * this suite track GEX for" must have a single answer, or the picker and the freshness check drift
 * into disagreeing about the same question.
 */
export const CURRENT_GEX_SYMBOLS_SQL = `SELECT DISTINCT symbol FROM gex_regime_history
   WHERE trade_date = (SELECT MAX(trade_date) FROM gex_regime_history) ORDER BY symbol`;

export function gexSymbols(config: ConsoleConfig): string[] {
  // Derived from what the RECORDER writes, not from every underlying the shared stream cache has
  // ever held. That cache is written by every module in the suite, so `DISTINCT underlying_symbol
  // FROM stream_chain` -- what this read until 2026-09-02 -- offered 35 symbols: earnings' single
  // names (META, AVGO, GOOG...), pmcc's leveraged ETFs, the overview's eleven sector ETFs, and
  // retired index experiments (NDX, RUT, IWM), most of them weeks stale. Only SPX has a live GEX
  // profile. Picking NDX would have computed a gamma profile off a five-week-old chain and
  // presented it as a claim about now, which is precisely what a GEX read must never be.
  //
  // Empty when the recorder has never run: a suite with no recorded GEX has nothing to profile,
  // and saying so is better than offering a list built from another module's leftovers.
  const p = path.join(config.paths.gexDir, "gex_history.db");
  if (!fs.existsSync(p)) return [];
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    return db
      .prepare<[], { symbol: string }>(CURRENT_GEX_SYMBOLS_SQL)
      .all()
      .map((r) => r.symbol)
      .filter((s) => s.length > 0);
  } catch {
    return [];
  } finally {
    db?.close();
  }
}
