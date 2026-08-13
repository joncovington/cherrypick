/**
 * Read-only mirrors of tastytrade watchlists: the user's account watchlists
 * plus a curated allowlist of public lists the user has pinned. Broker GETs
 * only (fine under the read-scoped token), cached in console.db with a TTL
 * and stale-served on broker failure so the page never depends on broker
 * availability. Row enrichment is pure reads: stream cache + the console's
 * own candle cache — zero broker calls.
 */

import type {
  SymbolCardPayload,
  TtWatchlistIndex,
  TtWatchlistPayload,
  TtWatchlistRow,
  TtWatchlistTab,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { getClient, hasCredential } from "../market/session.js";
import { cachedQuote } from "../readers/streamcache.js";
import {
  type TtWatchlistCacheRow,
  getTtWatchlist,
  getWatchlist,
  listPublicPins,
  listTtWatchlists,
  upsertTtWatchlist,
  candleLastBackfill,
  readOwnCandles,
  readTtMetrics,
  writeTtMetrics,
} from "../store/consoleDb.js";
import { readDailyCandles } from "../readers/scoutdb.js";
import { FRESH_S } from "./candles.js";

export const PUBLIC_ALLOWLIST = ["Liquid Symbols", "tasty Earnings", "High Options Volume"];

const TTL_S = 900;
const METRICS_TTL_S = 900;
export const LIVE_MAX_SYMBOLS = 30;

const SYMBOL_RE = /^[A-Z][A-Z0-9./]{0,9}$/;
const EQUITY_TYPES = new Set(["Equity", "ETF", "Index"]);

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number.parseFloat(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/** Parse one watchlist object's entries into (kept, skipped) symbol lists. */
export function parseEntries(raw: unknown): { symbols: string[]; skipped: string[] } {
  const symbols: string[] = [];
  const skipped: string[] = [];
  const wl = raw as Record<string, unknown>;
  const entries = (wl?.["watchlist-entries"] ?? []) as Array<Record<string, unknown>>;
  if (!Array.isArray(entries)) return { symbols, skipped };
  for (const e of entries) {
    const sym = typeof e?.["symbol"] === "string" ? e["symbol"].trim().toUpperCase() : "";
    const itype = typeof e?.["instrument-type"] === "string" ? e["instrument-type"] : "";
    // Instrument type is advisory (some lists omit it); the symbol regex is
    // the hard gate — it drops futures (/ES) and malformed entries.
    if (sym !== "" && SYMBOL_RE.test(sym) && (itype === "" || EQUITY_TYPES.has(itype))) {
      if (!symbols.includes(sym)) symbols.push(sym);
    } else if (sym !== "") {
      skipped.push(sym);
    }
  }
  return { symbols, skipped };
}

function asItems(raw: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(raw)) return raw as Array<Record<string, unknown>>;
  const root = raw as Record<string, unknown>;
  const data = (root?.["data"] ?? root) as Record<string, unknown>;
  const items = data?.["items"];
  if (Array.isArray(items)) return items as Array<Record<string, unknown>>;
  return [data];
}

interface FetchDeps {
  getAllWatchlists: () => Promise<unknown>;
  getPublicWatchlist: (name: string) => Promise<unknown>;
}

function defaultDeps(): FetchDeps {
  const client = getClient();
  return {
    getAllWatchlists: () => client.watchlistsService.getAllWatchlists(),
    getPublicWatchlist: (name: string) => client.watchlistsService.getPublicWatchlist(name),
  };
}

let lastError: string | null = null;

/**
 * Refresh the cached mirrors when the TTL has lapsed (or force=true).
 * Broker failure stale-serves whatever is cached; missing credential serves
 * cache only. Never throws.
 */
export async function refreshTtWatchlists(
  config: ConsoleConfig,
  opts: { force?: boolean; now?: number; deps?: FetchDeps } = {},
): Promise<void> {
  const now = opts.now ?? Date.now() / 1000;
  if (!hasCredential()) return;
  const cached = listTtWatchlists(config);
  const newest = cached.reduce((m, r) => Math.max(m, r.fetchedAt), 0);
  if (!opts.force && cached.length > 0 && now - newest < TTL_S) return;

  let deps: FetchDeps;
  try {
    deps = opts.deps ?? defaultDeps();
  } catch (err) {
    lastError = (err as Error).message;
    return;
  }

  try {
    const userRaw = await deps.getAllWatchlists();
    for (const wl of asItems(userRaw)) {
      const name = typeof wl["name"] === "string" ? wl["name"] : null;
      if (name === null) continue;
      const { symbols, skipped } = parseEntries(wl);
      upsertTtWatchlist(config, { key: `tt:${name}`, kind: "user", name, symbols, skipped, fetchedAt: now });
    }
    lastError = null;
  } catch (err) {
    lastError = (err as Error).message;
  }

  for (const name of listPublicPins(config)) {
    if (!PUBLIC_ALLOWLIST.includes(name)) continue;
    try {
      const raw = await deps.getPublicWatchlist(name);
      const item = asItems(raw)[0];
      const { symbols, skipped } = parseEntries(item);
      upsertTtWatchlist(config, {
        key: `public:${name}`,
        kind: "public",
        name,
        symbols,
        skipped,
        fetchedAt: now,
      });
      lastError = null;
    } catch (err) {
      lastError = (err as Error).message;
    }
  }
}

function toTab(row: TtWatchlistCacheRow, now: number): TtWatchlistTab {
  return {
    key: row.key,
    kind: row.kind,
    name: row.name,
    count: row.symbols.length,
    fetchedAt: new Date(row.fetchedAt * 1000).toISOString(),
    stale: now - row.fetchedAt > TTL_S * 2,
  };
}

export async function ttWatchlistIndex(config: ConsoleConfig): Promise<TtWatchlistIndex> {
  await refreshTtWatchlists(config);
  const now = Date.now() / 1000;
  const pins = listPublicPins(config);
  const tabs = listTtWatchlists(config)
    .filter((r) => r.kind === "user" || pins.includes(r.name))
    .map((r) => toTab(r, now));
  return { tabs, available: PUBLIC_ALLOWLIST, pins, credential: hasCredential() };
}

/** Resolve a screener/warm source string to a symbol list; null = unknown source. */
export function resolveSource(config: ConsoleConfig, source: string): string[] | null {
  if (source === "local") return getWatchlist(config);
  if (/^(tt|public):/.test(source)) {
    const row = getTtWatchlist(config, source);
    return row === null ? null : row.symbols;
  }
  return null;
}

interface MetricsDeps {
  getMarketMetrics: (symbols: string[]) => Promise<unknown>;
}

function defaultMetricsDeps(): MetricsDeps {
  const client = getClient();
  return {
    getMarketMetrics: (symbols: string[]) =>
      client.marketMetricsService.getMarketMetrics({ symbols: symbols.join(",") }) as Promise<unknown>,
  };
}

/**
 * IV rank / IV index / market cap per symbol: console.db cache first, one
 * batched market-metrics call for whatever is missing or past the TTL.
 * Broker failure serves whatever is cached; never throws.
 */
export async function metricsFor(
  config: ConsoleConfig,
  symbols: string[],
  opts: { now?: number; deps?: MetricsDeps } = {},
): Promise<ReturnType<typeof readTtMetrics>> {
  const now = opts.now ?? Date.now() / 1000;
  const cached = readTtMetrics(config, symbols);
  const missing = symbols.filter((s) => {
    const row = cached.get(s);
    return row === undefined || now - row.updatedAt > METRICS_TTL_S;
  });
  if (missing.length === 0 || !hasCredential()) return cached;

  let deps: MetricsDeps;
  try {
    deps = opts.deps ?? defaultMetricsDeps();
  } catch {
    return cached;
  }
  try {
    // extractResponseData in the SDK returns the items array directly;
    // asItems also tolerates the older {data:{items}} nesting.
    const raw = await deps.getMarketMetrics(missing);
    const items = asItems(raw);
    writeTtMetrics(
      config,
      items.map((m) => {
        const earnings = m["earnings"] as Record<string, unknown> | undefined;
        const reportDate = earnings?.["expected-report-date"];
        return {
          symbol: String(m["symbol"]),
          // 0..1 fraction despite the name (scout's boundary-normalization rule).
          ivRank: num(m["implied-volatility-index-rank"]),
          ivIndex: num(m["implied-volatility-index"]),
          marketCap: num(m["market-cap"]),
          liquidity: num(m["liquidity-rating"]),
          pe: num(m["price-earnings-ratio"]),
          divYield: num(m["dividend-yield"]),
          earningsDate: typeof reportDate === "string" ? reportDate : null,
        };
      }),
      now,
    );
    lastError = null;
  } catch (err) {
    lastError = (err as Error).message;
  }
  return readTtMetrics(config, symbols);
}

/** Quotes freshest-first (this process's tick memory, then scout's stream
 *  cache) + console/scout candle cache + TTL'd metrics. At most one batched
 *  broker call (metrics); everything else is pure reads. */
export async function buildRows(
  config: ConsoleConfig,
  symbols: string[],
  market?: import("../market/marketData.js").MarketDataService,
): Promise<TtWatchlistRow[]> {
  const nowS = Date.now() / 1000;
  const metrics = await metricsFor(config, symbols);
  return symbols.map((symbol) => {
    const m = metrics.get(symbol);
    const q = market?.recent(symbol, QUOTE_FRESH_S) ?? cachedQuote(config, symbol);
    let bars = readDailyCandles(config, symbol);
    let fresh = bars.length > 0; // scout keeps its own symbols current
    if (bars.length === 0) {
      bars = readOwnCandles(config, symbol);
      const lastBackfill = candleLastBackfill(config, symbol);
      fresh = lastBackfill !== null && nowS - lastBackfill < FRESH_S;
    }
    const valid = bars.filter((b) => Number.isFinite(b.c) && b.c > 0);
    const closes = valid.map((b) => b.c);
    const yearBars = valid.slice(-252);
    const eodClose = closes.length > 0 ? closes[closes.length - 1]! : null;
    const prevClose = closes.length > 1 ? closes[closes.length - 2]! : null;
    const lastBar = valid.length > 0 ? valid[valid.length - 1]! : null;
    const highs = yearBars.map((b) => (Number.isFinite(b.h) && b.h > 0 ? b.h : b.c));
    const lows = yearBars.map((b) => (Number.isFinite(b.l) && b.l > 0 ? b.l : b.c));
    const last = q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : null);
    return {
      symbol,
      last,
      bid: q?.bid ?? null,
      ask: q?.ask ?? null,
      eodClose,
      eodChangePct:
        eodClose !== null && prevClose !== null && prevClose > 0
          ? ((eodClose - prevClose) / prevClose) * 100
          : null,
      candleFresh: fresh,
      ivRank: m?.ivRank != null ? m.ivRank * 100 : null,
      ivIndex: m?.ivIndex != null ? m.ivIndex * 100 : null,
      volume: lastBar !== null && Number.isFinite(lastBar.v) && lastBar.v > 0 ? lastBar.v : null,
      // ETFs report market-cap 0 — absent, not tiny.
      marketCap: m?.marketCap != null && m.marketCap > 0 ? m.marketCap : null,
      yearHigh: highs.length >= 20 ? Math.max(...highs) : null,
      yearLow: lows.length >= 20 ? Math.min(...lows) : null,
    };
  });
}

const QUOTE_FRESH_S = 900;
const SWEEP_FLOOR_S = 120;
// Small chunks with a generous window: DXLink conflates and delivers lazily,
// and a wide batch on a short timeout comes back mostly empty.
const SWEEP_CHUNK = 25;
const SWEEP_TIMEOUT_MS = 8_000;
const SWEEP_MAX_SYMBOLS = 200;
const sweepLastAt = new Map<string, number>();

/**
 * Background quote sweep for tabs too large for per-viewer live subscriptions:
 * bounded snapshot batches whose ticks land in the market service's memory, so
 * the tab's next poll serves real last/bid/ask. Floored per tab key.
 */
async function sweepQuotes(
  market: import("../market/marketData.js").MarketDataService,
  key: string,
  symbols: string[],
): Promise<void> {
  const now = Date.now() / 1000;
  if (now - (sweepLastAt.get(key) ?? 0) < SWEEP_FLOOR_S) return;
  sweepLastAt.set(key, now);
  const capped = symbols.slice(0, SWEEP_MAX_SYMBOLS);
  for (let i = 0; i < capped.length; i += SWEEP_CHUNK) {
    await market.snapshotQuotes(capped.slice(i, i + SWEEP_CHUNK), SWEEP_TIMEOUT_MS);
  }
}

export async function ttWatchlistPayload(
  config: ConsoleConfig,
  key: string,
  market?: import("../market/marketData.js").MarketDataService,
): Promise<TtWatchlistPayload | null> {
  const row = getTtWatchlist(config, key);
  if (row === null) return null;
  const rows = await buildRows(config, row.symbols, market);
  const live = row.symbols.length <= LIVE_MAX_SYMBOLS;
  if (market !== undefined) {
    // Fire-and-forget collectors; both are floored, so polling stays cheap.
    if (!live) void sweepQuotes(market, key, row.symbols).catch(() => undefined);
    const stale = rows.filter((r) => !r.candleFresh).map((r) => r.symbol);
    if (stale.length > 0) {
      const { warmCandles } = await import("./candleWarm.js");
      void warmCandles(config, market, stale).catch(() => undefined);
    }
  }
  return {
    tab: toTab(row, Date.now() / 1000),
    rows,
    skipped: row.skipped,
    live,
  };
}

export function ttLastError(): string | null {
  return lastError;
}

/**
 * The builder's symbol summary card. Bars come through getDailyBars (scout
 * cache → console cache → one bounded DXLink backfill); metrics through the
 * TTL'd cache (at most one batched broker call). Trend uses the same
 * classifier as the symbol page.
 */
export async function symbolCard(
  config: ConsoleConfig,
  market: import("../market/marketData.js").MarketDataService,
  symbol: string,
): Promise<SymbolCardPayload> {
  const { getDailyBars } = await import("./candles.js");
  const { classifyTrend } = await import("../analytics/trend.js");
  const [{ bars }, metrics] = await Promise.all([
    getDailyBars(config, market, symbol),
    metricsFor(config, [symbol]),
  ]);
  const m = metrics.get(symbol);
  const valid = bars.filter((b) => Number.isFinite(b.c) && b.c > 0);
  const yearBars = valid.slice(-252);
  const closes = valid.map((b) => b.c);
  const eodClose = closes.length > 0 ? closes[closes.length - 1]! : null;
  const prevClose = closes.length > 1 ? closes[closes.length - 2]! : null;
  const lastBar = valid.length > 0 ? valid[valid.length - 1]! : null;
  const highs = yearBars.map((b) => (Number.isFinite(b.h) && b.h > 0 ? b.h : b.c));
  const lows = yearBars.map((b) => (Number.isFinite(b.l) && b.l > 0 ? b.l : b.c));
  const q = market.recent(symbol, QUOTE_FRESH_S) ?? cachedQuote(config, symbol);
  const trend = classifyTrend(closes);
  return {
    symbol,
    last: q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : null),
    eodClose,
    eodChangePct:
      eodClose !== null && prevClose !== null && prevClose > 0
        ? ((eodClose - prevClose) / prevClose) * 100
        : null,
    yearHigh: highs.length >= 20 ? Math.max(...highs) : null,
    yearLow: lows.length >= 20 ? Math.min(...lows) : null,
    volume: lastBar !== null && Number.isFinite(lastBar.v) && lastBar.v > 0 ? lastBar.v : null,
    ivRank: m?.ivRank != null ? m.ivRank * 100 : null,
    ivIndex: m?.ivIndex != null ? m.ivIndex * 100 : null,
    liquidity: m?.liquidity ?? null,
    // A provider zero means "not applicable" (an index has no P/E), not a real zero -- same
    // not-a-real-value guard marketCap already has below.
    pe: m?.pe != null && m.pe > 0 ? m.pe : null,
    // Dividend-yield convention is unverified against a live account; values
    // ≤ 1 are treated as fractions, larger ones as already percentage points.
    divYield: m?.divYield != null && m.divYield > 0 ? (m.divYield <= 1 ? m.divYield * 100 : m.divYield) : null,
    marketCap: m?.marketCap != null && m.marketCap > 0 ? m.marketCap : null,
    earningsDate: m?.earningsDate ?? null,
    trend1m: trend["1m"],
    trend6m: trend["6m"],
  };
}
