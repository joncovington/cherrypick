import fs from "node:fs";
import Database from "better-sqlite3";
import type { SourceFreshness } from "@console/shared";
import type { ConsoleConfig } from "../config.js";

/**
 * Read-only view over the Python streamer's stream_cache.db.
 * Opens per call and closes immediately: the DB is WAL and cheap to open,
 * and holding no handle means the console can never interfere with the writer.
 */
/** Streamer rows persist across weeks, so age-gate every read: the cache only
 *  holds symbols scout is (or once was) streaming, and a quote from a
 *  long-unsubscribed symbol is days old, not "last". The default tolerates a
 *  long weekend's worth of staleness for display; decision-adjacent callers
 *  (screener/chain-snapshot spot) pass a tight gate and fall back to a live
 *  DXLink snapshot or candle close instead. */
const DEFAULT_MAX_AGE_S = 4 * 86_400;

/** Last cached quote/trade for one symbol — the off-hours / DXLink-down fallback. */
export function cachedQuote(
  config: ConsoleConfig,
  symbol: string,
  maxAgeS: number = DEFAULT_MAX_AGE_S,
): { bid?: number; ask?: number; last?: number } | null {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return null;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const cutoff = Date.now() / 1000 - maxAgeS;
    const q = db
      .prepare<[string, number], Record<string, unknown>>(
        "SELECT bid, ask FROM stream_quotes WHERE symbol = ? AND updated_at >= ?",
      )
      .get(symbol, cutoff);
    const t = db
      .prepare<[string, number], Record<string, unknown>>(
        "SELECT last FROM stream_trades WHERE symbol = ? AND updated_at >= ?",
      )
      .get(symbol, cutoff);
    if (q === undefined && t === undefined) return null;
    const out: { bid?: number; ask?: number; last?: number } = {};
    if (typeof q?.["bid"] === "number") out.bid = q["bid"];
    if (typeof q?.["ask"] === "number") out.ask = q["ask"];
    if (typeof t?.["last"] === "number") out.last = t["last"];
    return Object.keys(out).length > 0 ? out : null;
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

export function streamerFreshness(config: ConsoleConfig): SourceFreshness {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) {
    return { key: "streamer", label: "Streamer", ageSeconds: null, present: false };
  }
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const row = db
      .prepare("SELECT MAX(updated_at) AS latest FROM stream_quotes")
      .get() as { latest: string | number | null };
    let ageSeconds: number | null = null;
    if (row.latest !== null) {
      const t = typeof row.latest === "number" ? row.latest * 1000 : Date.parse(row.latest);
      if (!Number.isNaN(t)) ageSeconds = Math.max(0, (Date.now() - t) / 1000);
    }
    return { key: "streamer", label: "Streamer", ageSeconds, present: true };
  } catch {
    return { key: "streamer", label: "Streamer", ageSeconds: null, present: true };
  } finally {
    db?.close();
  }
}
