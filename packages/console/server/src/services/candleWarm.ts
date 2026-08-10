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
let progressDone = 0;
let progressTotal = 0;
let lastResult: { warmed: number; failed: number; finishedAt: number } | null = null;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

let warming = false;

export async function warmCandles(
  config: ConsoleConfig,
  market: MarketDataService,
  symbols: string[],
  opts: { force?: boolean } = {},
): Promise<CandleWarmResult | { error: string }> {
  const start = Date.now();
  if (!opts.force && start - lastRunAt < WARM_FLOOR_MS) {
    return {
      error: `candle warm ran ${Math.round((start - lastRunAt) / 1000)}s ago — floor is ${WARM_FLOOR_MS / 1000}s`,
    };
  }
  if (warming) return { error: "candle warm already running" };
  if (symbols.length === 0) return { error: "no symbols to warm" };
  lastRunAt = start;
  warming = true;
  try {
    return await warmCandlesInner(config, market, symbols, start);
  } finally {
    warming = false;
  }
}

async function warmCandlesInner(
  config: ConsoleConfig,
  market: MarketDataService,
  symbols: string[],
  start: number,
): Promise<CandleWarmResult> {

  const capped = symbols.slice(0, WARM_MAX_SYMBOLS);
  progressDone = 0;
  progressTotal = capped.length;
  let warmed = 0;
  let skippedFresh = 0;
  let consecutiveEmpty = 0;
  const failed: string[] = [];

  for (const [i, symbol] of capped.entries()) {
    progressDone = i;
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
      let bars = await market.backfillDailyCandles(symbol, BACKFILL_DAYS);
      // Three empty results in a row is a silently dead feed, not three
      // delisted symbols — rebuild the session once and retry this one.
      if (bars.length === 0 && consecutiveEmpty >= 2) {
        await market.reconnectFeed();
        bars = await market.backfillDailyCandles(symbol, BACKFILL_DAYS);
      }
      if (bars.length === 0) {
        consecutiveEmpty += 1;
        failed.push(symbol);
      } else {
        consecutiveEmpty = 0;
        writeOwnCandles(config, symbol, bars);
        warmed += 1;
      }
    } catch {
      consecutiveEmpty += 1;
      failed.push(symbol);
    }
    await sleep(WARM_POLITENESS_MS);
  }

  lastResult = { warmed, failed: failed.length, finishedAt: Date.now() };
  return {
    requested: capped.length,
    warmed,
    skippedFresh,
    failed,
    tookMs: Date.now() - start,
  };
}

export function candleWarmStatus(): {
  running: boolean;
  progress: { done: number; total: number } | null;
  lastResult: { warmed: number; failed: number; finishedAt: number } | null;
} {
  return {
    running: warming,
    progress: warming ? { done: progressDone, total: progressTotal } : null,
    lastResult,
  };
}

// Continuous cadence: fresh symbols skip instantly, so an all-fresh sweep is
// a few cheap cache reads — only actual staleness (first boot, a new symbol,
// the 20h freshness lapse) costs broker calls.
const AUTO_INTERVAL_MS = 30 * 60_000;
const AUTO_FIRST_DELAY_MS = 60_000;
const AUTO_MAX_PASSES = 3;

/**
 * Self-healing warm loop: every 30 minutes (first check one minute after
 * boot) warms whatever part of the watchlist universe has gone stale. No
 * buttons, no fixed windows — a console started at any hour catches itself
 * up. A pass's failures are usually the time budget cutting the tail, so it
 * repeats up to three passes; no progress between passes means the failures
 * are real and more passes would just repeat them.
 */
export function startCandleWarmScheduler(
  config: ConsoleConfig,
  market: MarketDataService,
  log: (msg: string) => void,
): NodeJS.Timeout {
  let lastAutoAt = Date.now() - AUTO_INTERVAL_MS + AUTO_FIRST_DELAY_MS;
  const timer = setInterval(() => {
    void (async () => {
      if (warming || Date.now() - lastAutoAt < AUTO_INTERVAL_MS) return;
      lastAutoAt = Date.now();
      const { snapshotUniverse } = await import("./chainEod.js");
      const universe = snapshotUniverse(config);
      if (universe.length === 0) return;
      for (let pass = 0; pass < AUTO_MAX_PASSES; pass += 1) {
        const result = await warmCandles(config, market, universe, { force: true });
        if ("error" in result) {
          log(`candle warm: ${result.error}`);
          return;
        }
        if (result.warmed > 0 || result.failed.length > 0) {
          log(
            `candle warm pass ${pass + 1}: warmed ${result.warmed}, fresh ${result.skippedFresh}, failed ${result.failed.length} (${Math.round(result.tookMs / 1000)}s)`,
          );
        }
        if (result.failed.length === 0 || result.warmed === 0) return;
      }
    })();
  }, 60_000);
  timer.unref();
  return timer;
}
