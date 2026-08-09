/**
 * Daily bars for the symbol page: scout's cache first (streamer-before-API,
 * and scout already paid the backfill cost for its symbols), then the
 * console's own console.db candle cache, then a bounded DXLink backfill
 * stored there. Chart history only — nothing here informs a decision.
 */

import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { readDailyCandles } from "../readers/scoutdb.js";
import type { Bar } from "../analytics/levels.js";

const FRESH_S = 20 * 3600;

function openOwn(config: ConsoleConfig): Database.Database {
  fs.mkdirSync(config.paths.consoleData, { recursive: true });
  const db = new Database(path.join(config.paths.consoleData, "console.db"));
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS candles (
      symbol TEXT NOT NULL, ts INTEGER NOT NULL,
      o REAL, h REAL, l REAL, c REAL, v REAL,
      PRIMARY KEY (symbol, ts)
    );
    CREATE TABLE IF NOT EXISTS candle_meta (
      symbol TEXT PRIMARY KEY, last_backfill REAL NOT NULL
    );
  `);
  return db;
}

function ownBars(db: Database.Database, symbol: string): Bar[] {
  return db
    .prepare<[string], Record<string, unknown>>(
      "SELECT ts, o, h, l, c, v FROM candles WHERE symbol = ? ORDER BY ts",
    )
    .all(symbol)
    .map((r) => ({ t: Number(r["ts"]), o: Number(r["o"]), h: Number(r["h"]), l: Number(r["l"]), c: Number(r["c"]), v: Number(r["v"]) }));
}

export async function getDailyBars(
  config: ConsoleConfig,
  market: MarketDataService,
  symbol: string,
): Promise<{ bars: Bar[]; source: "scout" | "console" | "backfill" | "none" }> {
  const fromScout = readDailyCandles(config, symbol);
  if (fromScout.length > 0) return { bars: fromScout, source: "scout" };

  const db = openOwn(config);
  try {
    const meta = db
      .prepare<[string], { last_backfill: number }>("SELECT last_backfill FROM candle_meta WHERE symbol = ?")
      .get(symbol);
    if (meta !== undefined && Date.now() / 1000 - meta.last_backfill < FRESH_S) {
      const cached = ownBars(db, symbol);
      if (cached.length > 0) return { bars: cached, source: "console" };
    }

    const fresh = await market.backfillDailyCandles(symbol);
    if (fresh.length === 0) {
      const stale = ownBars(db, symbol);
      return stale.length > 0 ? { bars: stale, source: "console" } : { bars: [], source: "none" };
    }
    const insert = db.prepare(
      "INSERT OR REPLACE INTO candles (symbol, ts, o, h, l, c, v) VALUES (?, ?, ?, ?, ?, ?, ?)",
    );
    const tx = db.transaction((rows: typeof fresh) => {
      for (const b of rows) insert.run(symbol, b.t, b.o, b.h, b.l, b.c, b.v);
    });
    tx(fresh);
    db.prepare("INSERT OR REPLACE INTO candle_meta (symbol, last_backfill) VALUES (?, ?)").run(
      symbol,
      Date.now() / 1000,
    );
    return { bars: fresh, source: "backfill" };
  } finally {
    db.close();
  }
}
