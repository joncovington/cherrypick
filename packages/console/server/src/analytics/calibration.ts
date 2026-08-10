/**
 * Port of cherrypick.core.metrics + core.profiles' qualification: one metric
 * vocabulary for promotion evidence over NORMALIZED closed-trade records
 * ({netPnl, capital?, session?, slippage?}), so every tag on every module is
 * judged in the same units. Unknowns stay null, never 0 — a record without
 * capital contributes nothing to return-on-capital, and the coverage counts
 * say how much of the sample carries each datum.
 */

export interface NormalizedRecord {
  netPnl: number;
  capital: number | null;
  session: string;
  slippage: number | null;
}

export const QUALIFICATION_RULE = { minDays: 14, minWinRate: 0.6, minSample: 20 };

export function returnOnCapital(records: NormalizedRecord[]): number | null {
  const withCapital = records.filter((r) => r.capital !== null && r.capital > 0);
  if (withCapital.length === 0) return null;
  const totalCapital = withCapital.reduce((s, r) => s + r.capital!, 0);
  if (totalCapital <= 0) return null;
  return Math.round((withCapital.reduce((s, r) => s + r.netPnl, 0) / totalCapital) * 1e4) / 1e4;
}

/** Per-trade Sharpe, deliberately NOT annualized (discrete event trades). */
export function sharpe(values: number[]): number | null {
  const n = values.length;
  if (n < 2) return null;
  const mean = values.reduce((s, v) => s + v, 0) / n;
  const varr = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
  if (varr <= 0) return null;
  return Math.round((mean / Math.sqrt(varr)) * 1000) / 1000;
}

export function maxDrawdown(values: number[]): number {
  let running = 0;
  let peak = 0;
  let dd = 0;
  for (const v of values) {
    running += v;
    peak = Math.max(peak, running);
    dd = Math.max(dd, peak - running);
  }
  return Math.round(dd * 100) / 100;
}

export function sampleProgress(n: number, targets = [30, 100]): { n: number; targets: number[]; nextTarget: number | null; progress: number } {
  const sorted = [...targets].sort((a, b) => a - b);
  const next = sorted.find((t) => n < t) ?? null;
  return {
    n,
    targets: sorted,
    nextTarget: next,
    progress: next !== null ? Math.round(Math.min(n / next, 1) * 1e4) / 1e4 : 1,
  };
}

export interface CalibrationReading {
  sample: number;
  winRate: number | null;
  days: number;
  netPnl: number;
  netPnl2xSlippage: number;
  slippageCoverage: number;
  returnOnCapital: number | null;
  capitalCoverage: number;
  sharpe: number | null;
  maxDrawdown: number;
  sampleProgress: ReturnType<typeof sampleProgress>;
}

export function calibrationReading(records: NormalizedRecord[]): CalibrationReading {
  const ordered = [...records].sort((a, b) => a.session.localeCompare(b.session));
  const nets = ordered.map((r) => r.netPnl);
  const n = nets.length;
  const wins = nets.filter((v) => v > 0).length;
  const sessions = new Set(ordered.map((r) => r.session).filter((s) => s !== ""));
  const knownSlips = ordered.map((r) => r.slippage).filter((v): v is number => v !== null);
  return {
    sample: n,
    winRate: n > 0 ? Math.round((wins / n) * 1e4) / 1e4 : null,
    days: sessions.size,
    netPnl: Math.round(nets.reduce((s, v) => s + v, 0) * 100) / 100,
    // Slippage is linear, so a doubled fraction is net minus the recorded slippage.
    netPnl2xSlippage: Math.round((nets.reduce((s, v) => s + v, 0) - knownSlips.reduce((s, v) => s + v, 0)) * 100) / 100,
    slippageCoverage: knownSlips.length,
    returnOnCapital: returnOnCapital(ordered),
    capitalCoverage: ordered.filter((r) => r.capital !== null && r.capital > 0).length,
    sharpe: sharpe(nets),
    maxDrawdown: maxDrawdown(nets),
    sampleProgress: sampleProgress(n),
  };
}

export interface Check {
  value: number | null;
  threshold: number | string;
  pass: boolean;
}

function check(value: number | null | undefined, threshold: number): Check {
  return { value: value ?? null, threshold, pass: value !== null && value !== undefined && value >= threshold };
}

export interface Qualification {
  qualified: boolean;
  checks: Record<string, Check>;
}

export function qualifyOne(reading: CalibrationReading, rule = QUALIFICATION_RULE): Qualification {
  const checks: Record<string, Check> = {
    sample: check(reading.sample, rule.minSample),
    win_rate: check(reading.winRate, rule.minWinRate),
    days: check(reading.days, rule.minDays),
  };
  return { qualified: Object.values(checks).every((c) => c.pass), checks };
}

export interface Metric {
  name: "return_on_capital" | "net_pnl";
  value: number;
}

/** Prefers return-on-capital; net P&L is always numeric on a reading. */
export function metricOf(reading: CalibrationReading): Metric {
  if (reading.returnOnCapital !== null) return { name: "return_on_capital", value: reading.returnOnCapital };
  return { name: "net_pnl", value: reading.netPnl };
}

export function formatMetric(m: Metric | null): string {
  if (m === null) return "n/a (no champion reading)";
  return m.name === "return_on_capital" ? `${(m.value * 100).toFixed(1)}%` : m.value.toFixed(2);
}

export interface ChampionVerdict {
  champion: string | null;
  championMetric: Metric | null;
  challengers: Record<string, Qualification & { metric: Metric; beatsChampion: boolean }>;
  eligible: boolean;
  recommendation: string;
  reason: string;
}

/**
 * Champion/challenger comparison for a module whose config names a champion.
 * A champion with no reading has nothing to lose to — any challenger beats it.
 * The champion's own reading is never required to clear the rule itself.
 */
export function recommendChampion(
  readings: Record<string, CalibrationReading>,
  champion: string | null,
  margin = 0,
  rule = QUALIFICATION_RULE,
): ChampionVerdict {
  const championReading = champion !== null ? readings[champion] : undefined;
  const championMetric = championReading !== undefined ? metricOf(championReading) : null;
  const challengers: ChampionVerdict["challengers"] = {};
  for (const [tag, reading] of Object.entries(readings)) {
    if (tag === champion) continue;
    const q = qualifyOne(reading, rule);
    const metric = metricOf(reading);
    const beats = championMetric === null || metric.value - championMetric.value >= margin;
    challengers[tag] = { ...q, metric, beatsChampion: q.qualified && beats };
  }
  const winners = Object.entries(challengers).filter(([, c]) => c.beatsChampion);
  if (winners.length > 0) {
    const best = winners.reduce((a, b) => (b[1].metric.value > a[1].metric.value ? b : a));
    return {
      champion,
      championMetric,
      challengers,
      eligible: true,
      recommendation: `champion:${best[0]}`,
      reason: `${best[0]} qualified and beats champion ${champion} on ${best[1].metric.name} (${formatMetric(best[1].metric)} vs ${formatMetric(championMetric)}); recommending promotion.`,
    };
  }
  return {
    champion,
    championMetric,
    challengers,
    eligible: false,
    recommendation: `retain:${champion ?? "none"}`,
    reason:
      champion === null
        ? "no champion declared — parallel arms are qualified independently, never promoted against each other."
        : `no qualified challenger beats champion ${champion}; retaining.`,
  };
}
