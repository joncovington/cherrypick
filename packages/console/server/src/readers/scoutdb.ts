import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "./db.js";
import type { Bar } from "../analytics/levels.js";

/** Read-only over scout's own cache.db — the console is a reader, never the producer. */
export function readDailyCandles(config: ConsoleConfig, symbol: string): Bar[] {
  const dbPath = path.join(config.paths.scoutDir, "cache.db");
  return withReadOnlyDb<Bar[]>(dbPath, [], (db) =>
    db
      .prepare<[string], Record<string, unknown>>(
        "SELECT ts, o, h, l, c, v FROM candles WHERE symbol = ? AND period = '1d' ORDER BY ts",
      )
      .all(symbol)
      .map((r) => ({
        t: Number(r["ts"]),
        o: Number(r["o"]),
        h: Number(r["h"]),
        l: Number(r["l"]),
        c: Number(r["c"]),
        v: Number(r["v"]),
      }))
      .filter((b) => Number.isFinite(b.c) && b.c > 0),
  );
}

export function candleSymbols(config: ConsoleConfig): string[] {
  const dbPath = path.join(config.paths.scoutDir, "cache.db");
  return withReadOnlyDb<string[]>(dbPath, [], (db) =>
    db
      .prepare<[], { symbol: string }>("SELECT DISTINCT symbol FROM candles ORDER BY symbol")
      .all()
      .map((r) => r.symbol),
  );
}
