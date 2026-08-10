/**
 * Bulk EOD candle warming for a watchlist: sequential DXLink daily-candle
 * backfills into the console's own candle cache, so the screener and symbol
 * pages have ~1 year of history without per-row broker calls. Button-triggered
 * only, floored, politeness-spaced, and bounded by an overall time budget so
 * a dead feed can't pin the request open. Skips symbols that are already
 * fresh — repeat runs are cheap.
 */

import type { CandleWarmResult } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { candleCount, candleLastBackfill, writeOwnCandles } from "../store/consoleDb.js";
import { readDailyCandles } from "../readers/scoutdb.js";
import { FRESH_S } from "./candles.js";

const WARM_FLOOR_MS = 120_000;
const WARM_POLITENESS_MS = 400;
export const WARM_MAX_SYMBOLS = 250;
const WARM_BUDGET_MS = 10 * 60_000;
const BACKFILL_DAYS = 365;
const MIN_BARS = 200;

let lastRunAt = 0;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function warmCandles(
  config: ConsoleConfig,
  market: MarketDataService,
  symbols: string[],
): Promise<CandleWarmResult | { error: string }> {
  const start = Date.now();
  if (start - lastRunAt < WARM_FLOOR_MS) {
    return {
      error: `candle warm ran ${Math.round((start - lastRunAt) / 1000)}s ago — floor is ${WARM_FLOOR_MS / 1000}s`,
    };
  }
  if (symbols.length === 0) return { error: "no symbols to warm" };
  lastRunAt = start;

  const capped = symbols.slice(0, WARM_MAX_SYMBOLS);
  let warmed = 0;
  let skippedFresh = 0;
  const failed: string[] = [];

  for (const [i, symbol] of capped.entries()) {
    if (Date.now() - start > WARM_BUDGET_MS) {
      failed.push(...capped.slice(i));
      break;
    }
    // Scout's cache counts as warm: every read path serves it first, so a
    // console-side backfill for these symbols would never be read.
    if (readDailyCandles(config, symbol).length > 0) {
      skippedFresh += 1;
      continue;
    }
    const lastBackfill = candleLastBackfill(config, symbol);
    if (
      lastBackfill !== null &&
      Date.now() / 1000 - lastBackfill < FRESH_S &&
      candleCount(config, symbol) >= MIN_BARS
    ) {
      skippedFresh += 1;
      continue;
    }
    try {
      const bars = await market.backfillDailyCandles(symbol, BACKFILL_DAYS);
      if (bars.length === 0) {
        failed.push(symbol);
      } else {
        writeOwnCandles(config, symbol, bars);
        warmed += 1;
      }
    } catch {
      failed.push(symbol);
    }
    await sleep(WARM_POLITENESS_MS);
  }

  return {
    requested: capped.length,
    warmed,
    skippedFresh,
    failed,
    tookMs: Date.now() - start,
  };
}
