/**
 * Once-daily EOD chain snapshot over the watchlist universe, collected on the
 * console's OWN DXLink/REST session — the shared Python streamer is never
 * asked to carry these symbols. Per symbol: nested chain (REST), the 2–3
 * nearest monthly expirations, a ±15-strike window around spot, one bounded
 * quote+greeks snapshot, persisted to console.db keyed by ET trade date.
 * The screener's "EOD chain" mode then scans with zero per-symbol broker
 * calls. Scheduled to start ~15:30 ET on weekdays so collection finishes by
 * the bell; also button-triggered, floored either way.
 */

import { getClient, hasCredential } from "../market/session.js";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { cachedQuote } from "../readers/streamcache.js";
import {
  type ChainEodOptionRow,
  chainEodMeta,
  chainEodStatus,
  getBlacklistReason,
  getWatchlist,
  listTtWatchlists,
  writeChainEod,
} from "../store/consoleDb.js";
import { isMonthly, parseNestedChain, type NestedStrike } from "./screener.js";

const STRIKE_WINDOW = 15;
const MAX_EXPIRATIONS = 3;
const EXP_MIN_DTE = 7;
const EXP_MAX_DTE = 100;
const RUN_FLOOR_MS = 10 * 60_000;
const POLITENESS_MS = 300;
const RUN_BUDGET_MS = 35 * 60_000;
export const SNAPSHOT_MAX_SYMBOLS = 250;

// Scheduler: first tick at/after 15:30 ET on a weekday triggers the run.
const SCHED_START_MINUTES = 15 * 60 + 30;
const SCHED_END_MINUTES = 16 * 60;

let lastRunAt = 0;
let running = false;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface ChainSnapshotResult {
  tradeDate: string;
  requested: number;
  captured: number;
  skippedFresh: number;
  skipped: Array<{ symbol: string; reason: string }>;
  tookMs: number;
}

/** Current date/time in ET, independent of the host timezone. */
export function etNow(now = new Date()): { date: string; minutes: number; weekday: boolean } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hour12: false,
  }).formatToParts(now);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  const hour = Number(get("hour")) % 24;
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    minutes: hour * 60 + Number(get("minute")),
    weekday: !["Sat", "Sun"].includes(get("weekday")),
  };
}

/** Union of every list the console knows: local + cached tastytrade mirrors. */
export function snapshotUniverse(config: ConsoleConfig): string[] {
  const out = new Set<string>(getWatchlist(config));
  for (const wl of listTtWatchlists(config)) for (const s of wl.symbols) out.add(s);
  return [...out].sort();
}

function pickMonthlies(
  chains: Map<string, NestedStrike[]>,
  today: number,
): Array<{ expiration: string; strikes: NestedStrike[] }> {
  const monthlies: Array<{ expiration: string; dte: number; strikes: NestedStrike[] }> = [];
  for (const [iso, strikes] of chains) {
    const dte = Math.round((Date.parse(iso) - today) / 86_400_000);
    if (dte >= EXP_MIN_DTE && dte <= EXP_MAX_DTE && isMonthly(iso)) {
      monthlies.push({ expiration: iso, dte, strikes });
    }
  }
  monthlies.sort((a, b) => a.dte - b.dte);
  return monthlies.slice(0, MAX_EXPIRATIONS);
}

export async function runChainSnapshot(
  config: ConsoleConfig,
  market: MarketDataService,
  symbols: string[],
  opts: { force?: boolean } = {},
): Promise<ChainSnapshotResult | { error: string }> {
  const start = Date.now();
  if (running) return { error: "chain snapshot already running" };
  if (start - lastRunAt < RUN_FLOOR_MS) {
    return {
      error: `chain snapshot ran ${Math.round((start - lastRunAt) / 1000)}s ago — floor is ${RUN_FLOOR_MS / 1000}s`,
    };
  }
  if (!hasCredential()) return { error: "no console broker credential" };
  if (symbols.length === 0) return { error: "no symbols to snapshot" };
  lastRunAt = start;
  running = true;

  try {
    const tradeDate = etNow().date;
    const capped = symbols.slice(0, SNAPSHOT_MAX_SYMBOLS);
    const client = getClient();
    const skipped: Array<{ symbol: string; reason: string }> = [];
    let captured = 0;
    let skippedFresh = 0;

    for (const [i, symbol] of capped.entries()) {
      if (Date.now() - start > RUN_BUDGET_MS) {
        for (const rest of capped.slice(i)) skipped.push({ symbol: rest, reason: "run budget exhausted" });
        break;
      }
      const blacklisted = getBlacklistReason(config, symbol);
      if (blacklisted !== null) {
        skipped.push({ symbol, reason: `blacklisted: ${blacklisted}` });
        continue;
      }
      if (opts.force !== true && chainEodMeta(config, tradeDate, symbol) !== null) {
        skippedFresh += 1;
        continue;
      }

      let spot = cachedQuote(config, symbol)?.last ?? null;
      if (spot === null) {
        const snap = await market.snapshotQuotes([symbol], 4_000);
        const q = snap.get(symbol);
        spot = q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : null);
      }
      if (spot === null) {
        skipped.push({ symbol, reason: "no spot price" });
        continue;
      }

      await sleep(POLITENESS_MS);
      let expirations: Array<{ expiration: string; strikes: NestedStrike[] }>;
      try {
        const chainRaw: unknown = await client.instrumentsService.getNestedOptionChain(symbol);
        expirations = pickMonthlies(parseNestedChain(chainRaw).chains, Date.now());
      } catch (err) {
        skipped.push({ symbol, reason: `chain fetch failed: ${(err as Error).message}` });
        continue;
      }
      if (expirations.length === 0) {
        skipped.push({ symbol, reason: `no monthly expiration in ${EXP_MIN_DTE}-${EXP_MAX_DTE} DTE` });
        continue;
      }

      const rows: ChainEodOptionRow[] = [];
      const bySymbol = new Map<string, { expiration: string; strike: number; otype: "C" | "P" }>();
      for (const { expiration, strikes } of expirations) {
        const windowed = [...strikes]
          .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
          .slice(0, STRIKE_WINDOW * 2);
        for (const s of windowed) {
          if (s.callStreamer !== null) bySymbol.set(s.callStreamer, { expiration, strike: s.strike, otype: "C" });
          if (s.putStreamer !== null) bySymbol.set(s.putStreamer, { expiration, strike: s.strike, otype: "P" });
        }
      }
      const data = await market.snapshotOptionData([...bySymbol.keys()], 8_000);
      for (const [streamerSym, where] of bySymbol) {
        const d = data.get(streamerSym);
        const bid = d?.bid ?? null;
        const ask = d?.ask ?? null;
        rows.push({
          ...where,
          bid,
          ask,
          mid: bid !== null && ask !== null ? (bid + ask) / 2 : null,
          delta: d?.delta ?? null,
          iv: d?.iv ?? null,
        });
      }
      const withQuotes = rows.filter((r) => r.mid !== null).length;
      if (withQuotes === 0) {
        skipped.push({ symbol, reason: "no option quotes arrived" });
        continue;
      }
      writeChainEod(config, tradeDate, symbol, spot, rows);
      captured += 1;
      await sleep(POLITENESS_MS);
    }

    return { tradeDate, requested: capped.length, captured, skippedFresh, skipped, tookMs: Date.now() - start };
  } finally {
    running = false;
  }
}

export function chainSnapshotStatus(config: ConsoleConfig): {
  latest: { tradeDate: string; symbols: number } | null;
  running: boolean;
} {
  return { latest: chainEodStatus(config), running };
}

/**
 * In-process daily trigger: checks once a minute; a weekday tick in the
 * 15:30–16:00 ET window starts a run over the full universe unless today's
 * snapshot already covers it (the per-symbol freshness check makes an
 * interrupted run resume where it left off).
 */
export function startChainEodScheduler(
  config: ConsoleConfig,
  market: MarketDataService,
  log: (msg: string) => void,
): NodeJS.Timeout {
  const timer = setInterval(() => {
    const { minutes, weekday } = etNow();
    if (!weekday || minutes < SCHED_START_MINUTES || minutes >= SCHED_END_MINUTES) return;
    if (running) return;
    const universe = snapshotUniverse(config);
    if (universe.length === 0) return;
    void runChainSnapshot(config, market, universe).then((result) => {
      if ("error" in result) {
        if (!result.error.includes("floor")) log(`chain EOD snapshot: ${result.error}`);
      } else {
        log(
          `chain EOD snapshot ${result.tradeDate}: captured ${result.captured}, fresh ${result.skippedFresh}, skipped ${result.skipped.length} (${Math.round(result.tookMs / 1000)}s)`,
        );
      }
    });
  }, 60_000);
  timer.unref();
  return timer;
}
