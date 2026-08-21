/**
 * The statistics every module's performance surface computes the same way.
 *
 * `periodKey` was the reason to make this file: flies and meic each carried a byte-identical copy
 * under different names (`bucketKey` / `periodKey`, arguments swapped). A date-bucketing rule is
 * exactly the kind of duplicate that goes wrong quietly — two copies drifting on where a week
 * starts would group the same sessions differently on two pages, and both would look right.
 *
 * `median` and `stdev` were each in one place only. They live here because a performance surface
 * needs both and there is no second opinion worth having about either.
 */

/**
 * The bucket a trade date belongs to at a given granularity.
 *
 * Weekly buckets are MONDAY-anchored, deliberately: SQLite's `%W` anchors on Sunday and would put
 * a Sunday-to-Monday boundary through the middle of a trading week, splitting a week's sessions
 * across two buckets. Monthly is the ISO prefix; anything else is the day itself.
 */
export function periodKey(granularity: string, tradeDate: string): string {
  if (granularity === "monthly") return tradeDate.slice(0, 7);
  if (granularity === "weekly") {
    const d = new Date(tradeDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  return tradeDate;
}

/** The middle value, averaging the two middles on an even count. `null` for an empty set. */
export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1 ? ordered[mid]! : (ordered[mid - 1]! + ordered[mid]!) / 2;
}

/**
 * Sample standard deviation (n-1). `null` below two values — one observation has no dispersion,
 * and reporting 0 there would be the misleadingly-precise zero this suite's ledgers already refuse
 * to write.
 */
export function stdev(values: number[]): number | null {
  if (values.length < 2) return null;
  const m = values.reduce((s, v) => s + v, 0) / values.length;
  const varr = values.reduce((s, v) => s + (v - m) ** 2, 0) / (values.length - 1);
  return Math.sqrt(varr);
}

/**
 * The notional base the cumulative curve is drawn against.
 *
 * A DISPLAY CONSTANT, not a bankroll any of these books trade. Every module using this runs
 * one-lot sampling streams — MEIC's `open` and `width-5` each took 265 one-lot entries in a single
 * session — so calling the line "equity" would imply position sizing and compounding these
 * experiments deliberately do not do. The drawdown underneath it is real; the base is scaffolding,
 * and the cards say so in their titles.
 */
export const BANKROLL_BASE = 100_000;

export interface EquityPoint {
  date: string;
  netPnl: number;
  /** BANKROLL_BASE + cumulative net. See the constant — this is a drawing aid, not a balance. */
  equity: number;
  /** Peak-to-here, in dollars. Real regardless of the base above. */
  drawdown: number;
}

export interface RiskSummary {
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  recoveryFactor: number | null;
  sampleSize: number;
  /** Sharpe above 3 on a sample this small is a warning about the sample, not a result. */
  sharpeOverfitFlag: boolean;
}

export const EMPTY_RISK: RiskSummary = {
  sharpe: null,
  sortino: null,
  calmar: null,
  recoveryFactor: null,
  sampleSize: 0,
  sharpeOverfitFlag: false,
};

/** Daily net P&L, in date order, folded into a cumulative curve with its running drawdown. */
export function equityCurve(daily: Array<{ date: string; net: number }>): EquityPoint[] {
  let cum = 0;
  let peak = 0;
  return daily.map(({ date, net }) => {
    cum += net;
    peak = Math.max(peak, cum);
    return { date, netPnl: net, equity: BANKROLL_BASE + cum, drawdown: peak - cum };
  });
}

/**
 * Sharpe, Sortino, Calmar and recovery factor over a daily curve, annualized on 252 sessions.
 *
 * Every one of these returns `null` rather than 0 where it is undefined — no dispersion, no
 * downside days, no drawdown to recover from. A 0 there reads as "measured, and it was zero",
 * which is the misleadingly-precise zero this suite's ledgers already refuse to write.
 */
export function riskSummary(equity: EquityPoint[]): RiskSummary {
  const returns = equity.map((b) => b.netPnl / BANKROLL_BASE);
  const n = returns.length;
  if (n === 0) return EMPTY_RISK;

  const meanR = returns.reduce((s, v) => s + v, 0) / n;
  const sd = stdev(returns);
  const downside = returns.filter((r) => r < 0);
  const ddSd = downside.length >= 2 ? stdev(downside) : null;
  const maxDd = Math.max(...equity.map((b) => b.drawdown), 0);
  const annualized = returns.reduce((s, v) => s + v, 0) * (252 / n);
  const maxDdPct = maxDd / BANKROLL_BASE;
  const netTotal = equity.reduce((s, b) => s + b.netPnl, 0);
  const sharpe = sd !== null && sd !== 0 ? (meanR / sd) * Math.sqrt(252) : null;
  const round3 = (v: number): number => Math.round(v * 1000) / 1000;

  return {
    sharpe: sharpe !== null ? round3(sharpe) : null,
    sortino: ddSd !== null && ddSd !== 0 ? round3((meanR / ddSd) * Math.sqrt(252)) : null,
    calmar: maxDdPct > 0 ? round3(annualized / maxDdPct) : null,
    recoveryFactor: maxDd > 0 ? round3(netTotal / maxDd) : null,
    sampleSize: n,
    sharpeOverfitFlag: sharpe !== null && sharpe > 3,
  };
}
