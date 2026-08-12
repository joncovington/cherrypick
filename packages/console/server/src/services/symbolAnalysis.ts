/**
 * The symbol page's prose: a scan headline, one concrete price-action observation, and up to three
 * supporting bullets. Composes `analytics/narrative.ts` over data the console already has — daily
 * candles, clustered levels, the trend classifier, and the TTL'd metrics cache.
 *
 * Two deliberate differences from scout, both because the console holds different data:
 *
 * - **Realized volatility is computed here, not read.** Scout got `hv_30d` from its own metrics
 *   service; the console's metrics cache never stored it. Rather than drop the IV-vs-realized bullet
 *   — the sharpest of them, and the one an options tool should lead with — it is computed from the
 *   daily closes the console already holds. Same definition either way: annualized stdev of log
 *   returns.
 * - **Ex-dividend warnings degrade to absent.** The cache carries no ex-date, so
 *   `eventWarnings` gets null for it and says nothing rather than guessing. Absence of a warning is
 *   a real claim in that function, so this is a gap to close by caching the field, not by inventing
 *   a value.
 */
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { movingAverages, supportResistance, type Bar, type Level } from "../analytics/levels.js";
import { classifyTrend, type TrendGrade } from "../analytics/trend.js";
import {
  priceAction,
  scanHeadline,
  technicalBullet,
  optionsBullet,
  relativeStrengthBullet,
  eventWarnings,
  type ScanHeadline,
  type MetricsInfo,
  type EarningsInfo,
} from "../analytics/narrative.js";
import { getDailyBars } from "./candles.js";
import { metricsFor } from "./ttWatchlists.js";

/** The benchmark for relative strength. SPX is what scout compared against. */
const BENCHMARK = "SPX";
/** Trading days in the realized-vol window — the 30-day convention IV_30d is quoted against. */
const HV_WINDOW = 30;

export interface SymbolAnalysis {
  symbol: string;
  headline: ScanHeadline | null;
  priceAction: string | null;
  bullets: string[];
  trend1m: TrendGrade | null;
  trend6m: TrendGrade | null;
  /** Present so a reader can see what the IV-vs-realized bullet was judged against. */
  ivIndex: number | null;
  realizedVol: number | null;
  ivRank: number | null;
  earningsDate: string | null;
  stale: boolean;
}

/**
 * Annualized realized volatility from daily closes: stdev of log returns × √252. Null when there is
 * not a full window, because a partial-window vol quietly understates and would then be compared
 * against a full-window IV.
 */
export function realizedVol(closes: number[], window = HV_WINDOW): number | null {
  if (closes.length < window + 1) return null;
  const slice = closes.slice(-(window + 1));
  const returns: number[] = [];
  for (let i = 1; i < slice.length; i++) {
    const prev = slice[i - 1]!;
    const cur = slice[i]!;
    if (prev <= 0 || cur <= 0) return null;
    returns.push(Math.log(cur / prev));
  }
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, r) => a + (r - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(variance) * Math.sqrt(252);
}

function metricsInfo(
  m: { ivIndex: number | null; ivRank: number | null } | undefined,
  hv: number | null,
): MetricsInfo {
  return {
    // The cache stores these as 0..1 fractions, which is the shape narrative.ts expects.
    iv_30d: m?.ivIndex ?? null,
    hv_30d: hv,
    iv_rank: m?.ivRank ?? null,
  };
}

export async function buildSymbolAnalysis(
  config: ConsoleConfig,
  market: MarketDataService,
  symbol: string,
): Promise<SymbolAnalysis> {
  const [{ bars }, metrics] = await Promise.all([
    getDailyBars(config, market, symbol),
    metricsFor(config, [symbol]),
  ]);
  const valid: Bar[] = bars.filter((b) => Number.isFinite(b.c) && b.c > 0);
  const m = metrics.get(symbol);
  const closes = valid.map((b) => b.c);
  const trend = classifyTrend(closes);
  const hv = realizedVol(closes);
  const info = metricsInfo(m, hv);
  const earnings: EarningsInfo | null = m?.earningsDate
    ? { expected_report_date: m.earningsDate }
    : null;

  if (valid.length === 0) {
    return {
      symbol,
      headline: null,
      priceAction: null,
      bullets: [],
      trend1m: null,
      trend6m: null,
      ivIndex: m?.ivIndex ?? null,
      realizedVol: null,
      ivRank: m?.ivRank ?? null,
      earningsDate: m?.earningsDate ?? null,
      stale: true,
    };
  }

  const levels: Level[] = supportResistance(valid);

  // Best-effort: a missing benchmark drops one bullet rather than failing the page.
  let benchmarkCloses: number[] | null = null;
  try {
    const bench = await getDailyBars(config, market, BENCHMARK);
    const bc = bench.bars.filter((b) => Number.isFinite(b.c) && b.c > 0).map((b) => b.c);
    benchmarkCloses = bc.length > 0 ? bc : null;
  } catch {
    benchmarkCloses = null;
  }

  // Order matters: options context first — it is the layer a price-only narrative lacks, and the
  // one an options tool should lead with.
  const bullets = [
    optionsBullet(symbol, info),
    technicalBullet(symbol, valid),
    relativeStrengthBullet(symbol, closes, benchmarkCloses),
  ].filter((b): b is string => b !== null);

  return {
    symbol,
    headline: scanHeadline(symbol, trend["1m"], trend["6m"], valid),
    priceAction: priceAction(symbol, valid, levels, trend["6m"], earnings),
    bullets,
    trend1m: trend["1m"],
    trend6m: trend["6m"],
    ivIndex: m?.ivIndex ?? null,
    realizedVol: hv,
    ivRank: m?.ivRank ?? null,
    earningsDate: m?.earningsDate ?? null,
    stale: false,
  };
}

/** Builder-facing warnings for a chosen expiration. Empty means nothing detected — a real claim. */
export async function buildEventWarnings(
  config: ConsoleConfig,
  symbol: string,
  expiration: string,
): Promise<string[]> {
  const metrics = await metricsFor(config, [symbol]);
  const m = metrics.get(symbol);
  const earnings: EarningsInfo | null = m?.earningsDate
    ? { expected_report_date: m.earningsDate }
    : null;
  return eventWarnings(expiration, earnings, null);
}

/** Re-exported so the symbol route can serve overlays without importing levels itself. */
export { movingAverages, supportResistance };
