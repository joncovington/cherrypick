/**
 * GEX profile assembled entirely from the shared stream cache read-only —
 * same source and expiration-pick discipline as the gex module's provider:
 * the nearest expiration that actually has live greeks.
 */

import fs from "node:fs";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import path from "node:path";
import { computeGexProfile, volumeTotals, type ChainEntryInput } from "../analytics/gex.js";

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

export function buildGexProfile(config: ConsoleConfig, symbol: string): Record<string, unknown> {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return { ok: false, error: "stream cache missing" };
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");

    const trade = db
      .prepare<[string], Record<string, unknown>>("SELECT last, volume FROM stream_trades WHERE symbol = ?")
      .get(symbol);
    const spot = typeof trade?.["last"] === "number" ? trade["last"] : null;
    if (spot === null) return { ok: false, error: `no cached spot for ${symbol}` };

    // Nearest expiration with greeks coverage.
    const expirations = db
      .prepare<[string], { expiration: string }>(
        "SELECT DISTINCT expiration FROM stream_chain WHERE underlying_symbol = ? ORDER BY expiration",
      )
      .all(symbol)
      .map((r) => r.expiration);
    const greeksStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT gamma, iv FROM stream_greeks WHERE symbol = ?",
    );
    const oiStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT open_interest FROM stream_oi WHERE symbol = ?",
    );
    const volStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT volume FROM stream_trades WHERE symbol = ?",
    );

    // Prefer live expirations (today onward); fall back to the most recent
    // past one so a weekend still shows Friday's cached profile.
    const today = new Date().toISOString().slice(0, 10);
    const future = expirations.filter((e) => e >= today);
    const past = expirations.filter((e) => e < today).reverse();
    for (const expiration of [...future, ...past]) {
      const rows = db
        .prepare<[string, string], { streamer_symbol: string; data_json: string }>(
          "SELECT streamer_symbol, data_json FROM stream_chain WHERE underlying_symbol = ? AND expiration = ?",
        )
        .all(symbol, expiration);

      const entries: ChainEntryInput[] = [];
      const greeks = new Map<string, { gamma: number; iv: number }>();
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
        }
        const o = oiStmt.get(row.streamer_symbol);
        if (typeof o?.["open_interest"] === "number") oi.set(row.streamer_symbol, o["open_interest"]);
        const v = volStmt.get(row.streamer_symbol);
        if (typeof v?.["volume"] === "number") volume.set(row.streamer_symbol, v["volume"]);
      }
      if (greeks.size === 0) continue; // no live greeks on this expiration — try the next

      const profile = computeGexProfile(entries, greeks, oi, volume, spot);
      if (!profile.ok) continue;
      return {
        ok: true,
        symbol,
        spot,
        expiration,
        series: profile.series,
        totals: profile.totals,
        volumeTotals: volumeTotals(profile.series),
        spotHistory: spotHistory(config, symbol),
      };
    }
    return { ok: false, error: `no expiration with cached greeks for ${symbol}` };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  } finally {
    db?.close();
  }
}

export function gexSymbols(config: ConsoleConfig): string[] {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return [];
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    return db
      .prepare<[], { underlying_symbol: string }>(
        "SELECT DISTINCT underlying_symbol FROM stream_chain ORDER BY underlying_symbol",
      )
      .all()
      .map((r) => r.underlying_symbol);
  } catch {
    return [];
  } finally {
    db?.close();
  }
}
