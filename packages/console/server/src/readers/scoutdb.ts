import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "./db.js";
import type { Bar } from "../analytics/levels.js";

/**
 * Read-only over the candle cache the retired scout package left behind
 * (`~/.cherrypick/data/scout/cache.db`).
 *
 * **This is now a frozen legacy source.** The scout package was deleted on 2026-08-12 and nothing
 * writes that file any more, so it only ever gets older. It is still read because it is free history
 * that already exists on this machine — `services/candles.ts` tries the console's own cache first,
 * falls back here, and backfills from DXLink for anything neither has. A missing or empty file is an
 * ordinary outcome, not an error, which is why every read here degrades to `[]`.
 *
 * Deleting the file is safe; deleting this reader is safe once it stops returning anything useful.
 */
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
