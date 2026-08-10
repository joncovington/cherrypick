/**
 * The screener compute flow, following scout's screener_service discipline:
 * ONE batched market-metrics call for the whole watchlist, a zero-broker-call
 * pre-filter on IV rank and liquidity, chains fetched only for survivors
 * (nearest 30–45 DTE, preferring a standard monthly), one bounded DXLink
 * quote snapshot for a strike window around spot, then pure candidate build
 * and a multiplicative composite rank. Button-triggered only, with a run
 * floor so the button can't hammer the broker.
 */

import { getClient, hasCredential } from "../market/session.js";
import type { MarketDataService } from "../market/marketData.js";
import type { ConsoleConfig } from "../config.js";
import { cachedQuote } from "../readers/streamcache.js";
import {
  addToBlacklist,
  getBlacklistReason,
  chainEodMeta,
  chainEodStatus,
  readChainEod,
} from "../store/consoleDb.js";
import {
  type ChainOption,
  type Candidate,
  putCreditSpread,
  callCreditSpread,
  shortPut,
  directionalEdge,
  compositeScore,
} from "../analytics/strategies.js";
import { pop as popOf } from "../analytics/pop.js";
import type { Leg } from "../analytics/payoff.js";

const STRIKE_WINDOW = 15;
const DEFAULT_IV = 0.3;
const RUN_FLOOR_MS = 60_000;
const POLITENESS_MS = 250;

export interface ScreenerParams {
  dteMin: number;
  dteMax: number;
  wingWidthPct: number;
  minIvRank: number; // 0..100 scale, UI-side convention
  minLiquidity: number; // 0..4
  maxSymbols: number; // survivors cap after the metrics prefilter
  /** "live" fetches chains+quotes per survivor; "eod" reads the last daily chain snapshot. */
  quoteSource: "live" | "eod";
}

export interface ScreenerRow {
  symbol: string;
  spot: number;
  ivRank: number | null;
  liquidity: number | null;
  expectedMove: number;
  directionalEdge: number | null;
  candidate: Candidate & { pop: number | null; returnOnRisk: number | null; score: number | null };
}

export interface ScreenerResult {
  rows: ScreenerRow[];
  skipped: Array<{ symbol: string; reason: string }>;
  ranAt: string;
  source: string;
  quoteSource: "live" | "eod";
  /** Set in eod mode: the snapshot trade date the scan read from. */
  eodTradeDate?: string;
}

let lastRunAt = 0;

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number.parseFloat(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

export function isMonthly(expIso: string): boolean {
  const d = new Date(expIso + "T00:00:00Z");
  return d.getUTCDay() === 5 && d.getUTCDate() >= 15 && d.getUTCDate() <= 21;
}

export interface NestedStrike {
  strike: number;
  callStreamer: string | null;
  putStreamer: string | null;
  callOcc: string | null;
  putOcc: string | null;
}

export interface ParsedChain {
  chains: Map<string, NestedStrike[]>;
  /** true/false when the API stated expiration types; null when it didn't. */
  hasWeeklies: boolean | null;
}

export function parseNestedChain(raw: unknown): ParsedChain {
  const out = new Map<string, NestedStrike[]>();
  let sawType = false;
  let sawWeekly = false;
  // The SDK returns the items array directly; older shapes nest under data.items.
  let items: Array<Record<string, unknown>>;
  if (Array.isArray(raw)) {
    items = raw as Array<Record<string, unknown>>;
  } else {
    const root = raw as Record<string, unknown>;
    const data = (root?.["data"] ?? root) as Record<string, unknown>;
    items = (data?.["items"] ?? [data]) as Array<Record<string, unknown>>;
  }
  for (const item of items) {
    const expirations = item?.["expirations"] as Array<Record<string, unknown>> | undefined;
    if (!Array.isArray(expirations)) continue;
    for (const exp of expirations) {
      const iso = typeof exp["expiration-date"] === "string" ? exp["expiration-date"] : null;
      const strikes = exp["strikes"] as Array<Record<string, unknown>> | undefined;
      if (iso === null || !Array.isArray(strikes)) continue;
      const expType = exp["expiration-type"];
      if (typeof expType === "string" && expType !== "") {
        sawType = true;
        if (/weekly/i.test(expType)) sawWeekly = true;
      }
      out.set(
        iso,
        strikes
          .map((s) => ({
            strike: num(s["strike-price"]) ?? NaN,
            callStreamer: typeof s["call-streamer-symbol"] === "string" ? s["call-streamer-symbol"] : null,
            putStreamer: typeof s["put-streamer-symbol"] === "string" ? s["put-streamer-symbol"] : null,
            callOcc: typeof s["call"] === "string" ? s["call"] : null,
            putOcc: typeof s["put"] === "string" ? s["put"] : null,
          }))
          .filter((s) => Number.isFinite(s.strike)),
      );
    }
  }
  return { chains: out, hasWeeklies: sawType ? sawWeekly : null };
}

function pickExpiration(
  chains: Map<string, NestedStrike[]>,
  dteMin: number,
  dteMax: number,
): { expiration: string; dte: number; strikes: NestedStrike[] } | null {
  const today = Date.now();
  const candidates: Array<{ expiration: string; dte: number; strikes: NestedStrike[] }> = [];
  for (const [iso, strikes] of chains) {
    const dte = Math.round((Date.parse(iso) - today) / 86_400_000);
    if (dte >= dteMin && dte <= dteMax) candidates.push({ expiration: iso, dte, strikes });
  }
  if (candidates.length === 0) return null;
  const monthly = candidates.filter((c) => isMonthly(c.expiration));
  const pool = monthly.length > 0 ? monthly : candidates;
  const midTarget = (dteMin + dteMax) / 2;
  pool.sort((a, b) => Math.abs(a.dte - midTarget) - Math.abs(b.dte - midTarget));
  return pool[0]!;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function runScreener(
  config: ConsoleConfig,
  market: MarketDataService,
  symbols: string[],
  params: ScreenerParams,
  source = "local",
): Promise<ScreenerResult | { error: string }> {
  const now = Date.now();
  if (now - lastRunAt < RUN_FLOOR_MS) {
    return { error: `screener ran ${Math.round((now - lastRunAt) / 1000)}s ago — floor is ${RUN_FLOOR_MS / 1000}s` };
  }
  if (!hasCredential()) return { error: "no console broker credential" };
  if (symbols.length === 0) return { error: "watchlist is empty" };
  lastRunAt = now;

  const client = getClient();
  const skipped: Array<{ symbol: string; reason: string }> = [];
  const rows: ScreenerRow[] = [];

  // One batched metrics call for the entire list.
  let metricsBySymbol = new Map<string, Record<string, unknown>>();
  try {
    // The SDK's extractResponseData returns the items array directly; the
    // {data:{items}} fallback covers older shapes.
    const raw: unknown = await client.marketMetricsService.getMarketMetrics({ symbols: symbols.join(",") });
    let items: Array<Record<string, unknown>>;
    if (Array.isArray(raw)) {
      items = raw as Array<Record<string, unknown>>;
    } else {
      const root = raw as Record<string, unknown>;
      const data = (root?.["data"] ?? root) as Record<string, unknown>;
      items = (data?.["items"] ?? []) as Array<Record<string, unknown>>;
    }
    metricsBySymbol = new Map(items.map((m) => [String(m["symbol"]), m]));
  } catch (err) {
    return { error: `market metrics failed: ${(err as Error).message}` };
  }

  // Zero-broker-call prefilter over the whole list, then a survivors cap
  // (IV rank desc, nulls last) so large tastytrade lists cut hard before any
  // chain fetch.
  interface Prefiltered {
    symbol: string;
    ivRankFrac: number | null;
    liquidity: number | null;
    iv: number;
  }
  const prefiltered: Prefiltered[] = [];
  for (const symbol of symbols) {
    const blacklisted = getBlacklistReason(config, symbol);
    if (blacklisted !== null) {
      skipped.push({ symbol, reason: `blacklisted: ${blacklisted}` });
      continue;
    }
    const m = metricsBySymbol.get(symbol);
    const ivRankFrac = num(m?.["implied-volatility-index-rank"]);
    const liquidity = num(m?.["liquidity-rating"]);
    const iv = num(m?.["implied-volatility-index"]) ?? DEFAULT_IV;

    if (ivRankFrac !== null && ivRankFrac * 100 < params.minIvRank) {
      skipped.push({ symbol, reason: `iv rank ${(ivRankFrac * 100).toFixed(0)} < ${params.minIvRank}` });
      continue;
    }
    if (liquidity !== null && liquidity < params.minLiquidity) {
      skipped.push({ symbol, reason: `liquidity ${liquidity} < ${params.minLiquidity}` });
      continue;
    }
    prefiltered.push({ symbol, ivRankFrac, liquidity, iv });
  }
  prefiltered.sort((a, b) => (b.ivRankFrac ?? -1) - (a.ivRankFrac ?? -1));
  for (const cut of prefiltered.slice(params.maxSymbols)) {
    skipped.push({ symbol: cut.symbol, reason: `over maxSymbols cap (${params.maxSymbols})` });
  }
  const survivors = prefiltered.slice(0, params.maxSymbols);

  // EOD mode reads the last daily chain snapshot instead of fetching.
  const eodDate = params.quoteSource === "eod" ? (chainEodStatus(config)?.tradeDate ?? null) : null;
  if (params.quoteSource === "eod" && eodDate === null) {
    return { error: "no EOD chain snapshot on file — run one from the screener page first" };
  }

  for (const { symbol, ivRankFrac, liquidity, iv } of survivors) {
    let spot: number | null;
    let picked: { expiration: string; dte: number } | null;
    let options: ChainOption[];

    if (eodDate !== null) {
      const meta = chainEodMeta(config, eodDate, symbol);
      if (meta === null) {
        skipped.push({ symbol, reason: `no EOD chain snapshot for ${eodDate}` });
        continue;
      }
      spot = meta.spot;
      const stored = readChainEod(config, eodDate, symbol);
      const today = Date.now();
      const byExp = new Map<string, typeof stored>();
      for (const r of stored) {
        const list = byExp.get(r.expiration) ?? [];
        list.push(r);
        byExp.set(r.expiration, list);
      }
      const midTarget = (params.dteMin + params.dteMax) / 2;
      let best: { expiration: string; dte: number } | null = null;
      for (const expiration of byExp.keys()) {
        const dte = Math.round((Date.parse(expiration) - today) / 86_400_000);
        if (dte < params.dteMin || dte > params.dteMax) continue;
        if (best === null || Math.abs(dte - midTarget) < Math.abs(best.dte - midTarget)) {
          best = { expiration, dte };
        }
      }
      if (best === null) {
        skipped.push({ symbol, reason: `snapshot has no expiration in ${params.dteMin}-${params.dteMax} DTE` });
        continue;
      }
      picked = best;
      options = (byExp.get(best.expiration) ?? []).map((r) => ({
        strike: r.strike,
        optionType: r.otype,
        mid: r.mid,
        occSymbol: null,
      }));
    } else {
      // Spot: stream cache first; DXLink snapshot only if missing.
      spot = cachedQuote(config, symbol)?.last ?? null;
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
      let pickedLive: { expiration: string; dte: number; strikes: NestedStrike[] } | null;
      try {
        const chainRaw: unknown = await client.instrumentsService.getNestedOptionChain(symbol);
        const parsed = parseNestedChain(chainRaw);
        // Learned blacklist: the API stated its expiration types and none were
        // weekly — remember that so future runs skip the symbol before any
        // broker call. User-clearable via the blacklist endpoint.
        if (parsed.hasWeeklies === false) {
          addToBlacklist(config, symbol, "no weekly options");
          skipped.push({ symbol, reason: "no weekly options (blacklisted for future runs)" });
          continue;
        }
        pickedLive = pickExpiration(parsed.chains, params.dteMin, params.dteMax);
      } catch (err) {
        skipped.push({ symbol, reason: `chain fetch failed: ${(err as Error).message}` });
        continue;
      }
      if (pickedLive === null) {
        skipped.push({ symbol, reason: `no expiration in ${params.dteMin}-${params.dteMax} DTE` });
        continue;
      }
      picked = { expiration: pickedLive.expiration, dte: pickedLive.dte };

      // Window ±STRIKE_WINDOW strikes around spot, snapshot their quotes once.
      const windowed = [...pickedLive.strikes]
        .sort((a, b) => Math.abs(a.strike - spot!) - Math.abs(b.strike - spot!))
        .slice(0, STRIKE_WINDOW * 2)
        .sort((a, b) => a.strike - b.strike);
      const streamerSymbols = windowed.flatMap((s) =>
        [s.callStreamer, s.putStreamer].filter((x): x is string => x !== null),
      );
      const quotes = await market.snapshotQuotes(streamerSymbols, 6_000);
      const midOf = (sym: string | null): number | null => {
        if (sym === null) return null;
        const q = quotes.get(sym);
        if (q?.bid === undefined || q.ask === undefined) return null;
        return (q.bid + q.ask) / 2;
      };
      options = windowed.flatMap((s) => [
        { strike: s.strike, optionType: "C" as const, mid: midOf(s.callStreamer), occSymbol: s.callOcc },
        { strike: s.strike, optionType: "P" as const, mid: midOf(s.putStreamer), occSymbol: s.putOcc },
      ]);
    }

    const t = picked.dte / 365;
    const expectedMove = spot * iv * Math.sqrt(t);
    const edge = directionalEdge(options, spot, expectedMove);

    const candidates = [
      putCreditSpread(options, spot, expectedMove, params.wingWidthPct, picked.expiration, picked.dte),
      callCreditSpread(options, spot, expectedMove, params.wingWidthPct, picked.expiration, picked.dte),
      shortPut(options, spot, expectedMove, picked.expiration, picked.dte),
    ].filter((c): c is Candidate => c !== null);
    if (candidates.length === 0) {
      skipped.push({ symbol, reason: "no viable candidate (missing quotes in window?)" });
      continue;
    }

    for (const candidate of candidates) {
      const legs: Leg[] = candidate.legs.map((l) => ({
        kind: l.kind,
        quantity: l.quantity,
        price: l.price,
        strike: l.strike,
      }));
      const popVal = popOf(legs, spot, iv, t, 0.04);
      const ror =
        candidate.maxRisk !== null && candidate.maxRisk > 0 ? candidate.credit / candidate.maxRisk : null;
      const score =
        ror !== null ? compositeScore(ror, popVal, ivRankFrac ?? 0.05, liquidity) : null;
      rows.push({
        symbol,
        spot,
        ivRank: ivRankFrac !== null ? ivRankFrac * 100 : null,
        liquidity,
        expectedMove,
        directionalEdge: edge,
        candidate: { ...candidate, pop: popVal, returnOnRisk: ror, score },
      });
    }
    if (eodDate === null) await sleep(POLITENESS_MS);
  }

  rows.sort((a, b) => (b.candidate.score ?? -1) - (a.candidate.score ?? -1));
  return {
    rows,
    skipped,
    ranAt: new Date().toISOString(),
    source,
    quoteSource: params.quoteSource,
    ...(eodDate !== null ? { eodTradeDate: eodDate } : {}),
  };
}
