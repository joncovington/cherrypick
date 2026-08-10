/**
 * Daily bars for the symbol page: scout's cache first (streamer-before-API,
 * and scout already paid the backfill cost for its symbols), then the
 * console's own console.db candle cache, then a bounded DXLink backfill
 * stored there. Chart history only — nothing here informs a decision.
 */

import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { readDailyCandles } from "../readers/scoutdb.js";
import type { Bar } from "../analytics/levels.js";
import {
  readOwnCandles,
  writeOwnCandles,
  candleLastBackfill,
} from "../store/consoleDb.js";

export const FRESH_S = 20 * 3600;

export async function getDailyBars(
  config: ConsoleConfig,
  market: MarketDataService,
  symbol: string,
): Promise<{ bars: Bar[]; source: "scout" | "console" | "backfill" | "none" }> {
  const fromScout = readDailyCandles(config, symbol);
  if (fromScout.length > 0) return { bars: fromScout, source: "scout" };

  const lastBackfill = candleLastBackfill(config, symbol);
  if (lastBackfill !== null && Date.now() / 1000 - lastBackfill < FRESH_S) {
    const cached = readOwnCandles(config, symbol);
    if (cached.length > 0) return { bars: cached, source: "console" };
  }

  const fresh = await market.backfillDailyCandles(symbol);
  if (fresh.length === 0) {
    const stale = readOwnCandles(config, symbol);
    return stale.length > 0 ? { bars: stale, source: "console" } : { bars: [], source: "none" };
  }
  writeOwnCandles(config, symbol, fresh);
  return { bars: fresh, source: "backfill" };
}
