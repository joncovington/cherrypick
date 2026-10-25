import type { ModuleSummarySlice, SummaryArmRow, SummaryBreakdown, SummaryCost, SummaryHero, SummaryShape, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";

/**
 * The shared half of the simplified-view summaries: the window, the session neighbours, and the
 * roll-up over a module's settled trades. The other half — which trades, which KPIs, in which
 * words — is each module's own reader (`readers/summary/<module>.ts`).
 *
 * A module reader hands this its settled trades as `SummaryTrade`s taken from ITS OWN history
 * reader's money columns (TradeMoney), so the summary and the module's history table are one
 * arithmetic and cannot disagree. Nothing here re-derives a fee or a net.
 */

export interface SummaryQuery {
  mode: TradingMode;
  range: string | undefined;
  /** The page's session; null = the module's latest session with settled rows. */
  day: string | null;
  /** The page header's scope, absent/null = every arm. Applied post-fetch, so a reader whose
   *  trades carry an `arm` narrows the same way whichever module it is. */
  arm?: string | null;
  /** The header's symbol, absent/null = every symbol. Only a reader whose trades carry a `symbol`
   *  can honour it; a single-symbol book ignores it rather than filtering itself to nothing. */
  symbol?: string | null;
  /** The header's era, absent/null = the module's own default era. Only the readers with an era
   *  model (flies today) read it. */
  era?: string | null;
}

export type SummaryReader = (config: ConsoleConfig, q: SummaryQuery) => ModuleSummarySlice;

/**
 * The page scope applied to a reader's settled trades: the header's arm and symbol, once, in one
 * place, so every module's summary narrows the same way and no reader re-implements it. A module
 * whose trades carry no `symbol` (curve's VXX, bwb's SPX) is not filtered on one it never had.
 */
export function scopeTrades<T extends SummaryTrade>(trades: readonly T[], q: SummaryQuery): T[] {
  const arm = q.arm ?? null;
  const symbol = q.symbol ?? null;
  if (arm === null && symbol === null) return [...trades];
  return trades.filter(
    (t) => (arm === null || t.arm === arm) && (symbol === null || t.symbol == null || t.symbol === symbol),
  );
}

/**
 * The same scope as SQL, for the counts a reader takes straight from the ledger instead of rolling
 * them up from `SummaryTrade[]` — the open count, the session list behind the day arrows, and the
 * activity tiles. Without this the page would show two scopes at once: a narrowed hero above
 * unscoped counters, which the money layout does not allow.
 *
 * `cols.symbol` is null for a single-symbol book, matching `scopeTrades`: it is not filtered on a
 * symbol it never had. `conditions` is the bare `col = ?` list for a caller that has to embed them
 * in its own subquery.
 */
export function scopeClauses(
  q: SummaryQuery,
  cols: { arm: string; symbol: string | null },
): { sql: string; params: string[]; conditions: string[] } {
  const conditions: string[] = [];
  const params: string[] = [];
  if (q.arm != null && q.arm !== "") {
    conditions.push(`${cols.arm} = ?`);
    params.push(q.arm);
  }
  if (q.symbol != null && q.symbol !== "" && cols.symbol !== null) {
    conditions.push(`${cols.symbol} = ?`);
    params.push(q.symbol);
  }
  return { sql: conditions.length > 0 ? ` AND ${conditions.join(" AND ")}` : "", params, conditions };
}

/** One settled trade, in the module's own money layout (per-row TradeMoney). */
export interface SummaryTrade {
  /** The ET session the trade is booked to (its close/settle session). */
  session: string;
  arm: string;
  /** The underlying, where the module's own rows carry one. Absent = a single-symbol book. */
  symbol?: string | null;
  gross: number;
  /** Trading fees, settlement folded in where the split was never recorded. Null = n/r. */
  fees: number | null;
  /** Null = never recorded (stays inside fees). */
  settlement: number | null;
  /** Slippage CHARGED as a cost (bwb, calendars, curve, earnings, pmcc); null where it is inside
   *  gross (flies) or was not recorded. */
  slippage: number | null;
  /** The history reader's own net for the row. */
  net: number;
}

/**
 * The calendar window a range names, widening from the page's SESSION (so the day arrows move
 * week/month/year too); ET today when no session is resolved. "all" is unbounded; an unknown
 * range falls back to the day rather than silently widening.
 */
export function summaryWindow(range: string | undefined, day: string | null): { from: string | null; to: string | null; label: string } {
  const nowEt = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const base = day ?? iso(new Date(Date.UTC(nowEt.getFullYear(), nowEt.getMonth(), nowEt.getDate())));
  const [y, m, d] = base.split("-").map(Number) as [number, number, number];
  const monday = new Date(Date.UTC(y, m - 1, d));
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  switch (range) {
    case "week":
      return { from: iso(monday), to: iso(sunday), label: `week of ${iso(monday)}` };
    case "month":
      return { from: `${base.slice(0, 8)}01`, to: iso(new Date(Date.UTC(y, m, 0))), label: `month of ${base.slice(0, 7)}` };
    case "year":
      return { from: `${base.slice(0, 4)}-01-01`, to: `${base.slice(0, 4)}-12-31`, label: base.slice(0, 4) };
    case "all":
      return { from: null, to: null, label: "all time" };
    default:
      return day === null ? { from: null, to: null, label: "all time" } : { from: day, to: day, label: "day" };
  }
}

/** The page's session and its neighbours among the sessions that have rows (ascending input). */
export function sessionNeighbours(sessions: readonly string[], day: string | null): { day: string | null; prev: string | null; next: string | null } {
  const sorted = [...new Set(sessions)].sort();
  const at = day ?? sorted[sorted.length - 1] ?? null;
  if (at === null) return { day: null, prev: null, next: null };
  const prev = [...sorted].reverse().find((s) => s < at) ?? null;
  const next = sorted.find((s) => s > at) ?? null;
  return { day: at, prev, next };
}

/** Is a session inside a window (inclusive, open ends)? */
export function inWindow(session: string, w: { from: string | null; to: string | null }): boolean {
  return (w.from === null || session >= w.from) && (w.to === null || session <= w.to);
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** Two-sided 95% Student's t critical values for 1..29 degrees of freedom; 1.96 from 30 on. */
const T95 = [
  12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.16, 2.145, 2.131,
  2.12, 2.11, 2.101, 2.093, 2.086, 2.08, 2.074, 2.069, 2.064, 2.06, 2.056, 2.052, 2.048, 2.045,
];
export function t95(df: number): number {
  return df >= 1 && df <= T95.length ? T95[df - 1]! : 1.96;
}

/** The Wilson 95% interval of a proportion (fractions); null with no trials. */
export function wilson95(successes: number, n: number): [number, number] | null {
  if (n <= 0) return null;
  const z = 1.96;
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

const round4 = (v: number) => Math.round(v * 10000) / 10000;

/** The per-trade nets' shape (SummaryShape): median, the average win and loss, the worst trade, and
 *  how sure the window's figures are -- net per trade with its 95% interval and t, the win rate's
 *  interval, and a skew flag in both directions (B2). A side with no trade is null, never zero; a
 *  figure the sample is too small for is null too. Shared with MEIC's own summary read. */
export function shapeOf(nets: readonly number[]): SummaryShape {
  const n = nets.length;
  if (n === 0) {
    return {
      median: null, avgWin: null, avgLoss: null, worst: null,
      n: 0, mean: null, sd: null, ci95: null, t: null, winRateCi95: null, skewness: null, skew: null,
    }; // prettier-ignore
  }
  const sorted = [...nets].sort((a, b) => a - b);
  const mid = Math.floor(n / 2);
  const median = n % 2 === 1 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
  const wins = nets.filter((x) => x > 0);
  const losses = nets.filter((x) => x < 0);
  const avg = (xs: number[]) => (xs.length > 0 ? round2(xs.reduce((s, x) => s + x, 0) / xs.length) : null);
  const mean = nets.reduce((s, x) => s + x, 0) / n;
  const sd = n >= 2 ? Math.sqrt(nets.reduce((s, x) => s + (x - mean) ** 2, 0) / (n - 1)) : null;
  const se = sd !== null && sd > 0 ? sd / Math.sqrt(n) : null;
  const half = se !== null ? t95(n - 1) * se : null;
  let skewness: number | null = null;
  if (n >= 3 && sd !== null && sd > 0) {
    const m3 = nets.reduce((s, x) => s + ((x - mean) / sd) ** 3, 0);
    skewness = (n / ((n - 1) * (n - 2))) * m3; // adjusted Fisher-Pearson
  }
  return {
    median: round2(median),
    avgWin: avg(wins),
    avgLoss: avg(losses),
    worst: round2(sorted[0]!),
    n,
    mean: round2(mean),
    sd: sd === null ? null : round2(sd),
    ci95: half === null ? null : [round2(mean - half), round2(mean + half)],
    t: se === null ? null : round2(mean / se),
    winRateCi95: (() => {
      const ci = wilson95(wins.length, n);
      return ci === null ? null : [round4(ci[0]), round4(ci[1])];
    })(),
    skewness: skewness === null ? null : round2(skewness),
    skew: skewness === null ? null : skewness <= -1 ? "left" : skewness >= 1 ? "right" : null,
  };
}

/** A gauge's worst-trade figure: the window's single worst net. */
export function worstNet(trades: readonly { net: number }[]): number | null {
  return trades.length > 0 ? round2(Math.min(...trades.map((t) => t.net))) : null;
}

/** Sum a cost line, n/r when any row did not record it. */
function sumRecorded(values: Array<number | null>): number | null {
  if (values.some((v) => v === null)) return null;
  return round2(values.reduce<number>((s, v) => s + (v ?? 0), 0));
}

/**
 * The hero over settled trades. `chargesSlippage` says whether the module subtracts slippage as a
 * cost (it then gets its own line) or carries it inside gross (no line). Net is the rows' own
 * nets summed, so the hero equals the history table's totals for the same window.
 */
/** Mean R and its interval over the trades `riskOf` gives a positive max loss for (B3). */
export function rOf<T extends SummaryTrade>(
  trades: readonly T[],
  riskOf: (t: T) => number | null | undefined,
): { meanR: number | null; rN: number; rCi95: [number, number] | null } {
  const rs: number[] = [];
  for (const t of trades) {
    const risk = riskOf(t);
    if (risk != null && risk > 0) rs.push(t.net / risk);
  }
  // Its own arithmetic, not shapeOf's: that rounds to cents, and R lives below one.
  const r4 = (v: number) => Math.round(v * 10000) / 10000;
  const n = rs.length;
  if (n === 0) return { meanR: null, rN: 0, rCi95: null };
  const mean = rs.reduce((a, b) => a + b, 0) / n;
  const sd = n >= 2 ? Math.sqrt(rs.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : 0;
  const half = sd > 0 ? (t95(n - 1) * sd) / Math.sqrt(n) : null;
  return { meanR: r4(mean), rN: n, rCi95: half === null ? null : [r4(mean - half), r4(mean + half)] };
}

export function heroOf<T extends SummaryTrade>(
  trades: readonly T[],
  chargesSlippage: boolean,
  open: number,
  riskOf?: (t: T) => number | null | undefined,
): SummaryHero {
  const costs: SummaryCost[] = [
    { label: "fees", amount: sumRecorded(trades.map((t) => t.fees)) },
    {
      label: "settlement",
      amount: sumRecorded(trades.map((t) => t.settlement)),
      note: "n/r: a trade never recorded its settlement split; it is inside fees",
    },
  ];
  if (chargesSlippage) {
    costs.push({ label: "slippage (charged)", amount: sumRecorded(trades.map((t) => t.slippage)) });
  }
  const bySession = new Map<string, number>();
  for (const t of trades) bySession.set(t.session, (bySession.get(t.session) ?? 0) + t.net);
  const dayNets = [...bySession.values()];
  return {
    net: round2(trades.reduce((s, t) => s + t.net, 0)),
    gross: round2(trades.reduce((s, t) => s + t.gross, 0)),
    costs,
    trades: trades.length,
    wins: trades.filter((t) => t.net > 0).length,
    sessions: bySession.size,
    daysWon: dayNets.filter((n) => n > 0).length,
    daysLost: dayNets.filter((n) => n < 0).length,
    open,
    shape: { ...shapeOf(trades.map((t) => t.net)), ...(riskOf ? rOf(trades, riskOf) : {}) },
    basis: null,
  };
}

/** A breakdown: settled trades grouped by a module-chosen key, net and win rate per bucket. */
export function breakdownOf<T extends SummaryTrade>(
  title: string,
  column: string,
  trades: readonly T[],
  key: (t: T) => string | null,
  order?: readonly string[],
): SummaryBreakdown {
  const groups = new Map<string, T[]>();
  for (const t of trades) {
    const k = key(t);
    if (k === null) continue;
    const g = groups.get(k) ?? [];
    g.push(t);
    groups.set(k, g);
  }
  const keys = order !== undefined ? order.filter((k) => groups.has(k)) : [...groups.keys()].sort();
  return {
    title,
    column,
    rows: keys.map((bucket) => {
      const g = groups.get(bucket) ?? [];
      const net = round2(g.reduce((s, t) => s + t.net, 0));
      return {
        bucket,
        trades: g.length,
        net,
        winPct: g.length > 0 ? (g.filter((t) => t.net > 0).length / g.length) * 100 : null,
        avgNet: g.length > 0 ? round2(net / g.length) : null,
      };
    }),
  };
}

/** Every arm's net, best first; footed so the rows add up to the hero's net to the cent. */
export function armsOf(trades: readonly SummaryTrade[], retired: readonly string[] = []): SummaryArmRow[] {
  const rows = breakdownOf("by arm", "arm", trades, (t) => t.arm).rows.map((r) => ({
    arm: r.bucket,
    trades: r.trades,
    net: r.net,
    winPct: r.winPct,
    retired: retired.includes(r.bucket),
  }));
  const total = round2(trades.reduce((s, t) => s + t.net, 0));
  const drift = Math.round(total * 100) - Math.round(rows.reduce((s, r) => s + r.net, 0) * 100);
  if (drift !== 0 && rows.length > 0) {
    const i = rows.reduce((best, r, j) => (Math.abs(r.net) > Math.abs(rows[best]!.net) ? j : best), 0);
    rows[i]!.net = round2(rows[i]!.net + drift / 100);
  }
  return rows.sort((a, b) => b.net - a.net);
}

/** The ET weekday of an ISO session, Mon..Fri (null on a weekend). */
export function weekdayOf(session: string): string | null {
  const d = new Date(`${session}T12:00:00Z`).getUTCDay();
  return ["", "Mon", "Tue", "Wed", "Thu", "Fri", ""][d] || null;
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"] as const;

/** An empty slice for a module whose ledger is missing: every card says "no rows", never zero. */
export function emptySlice(module: string, mode: TradingMode, unit: string, w: { from: string | null; to: string | null; label: string }): ModuleSummarySlice {
  return {
    module,
    mode,
    window: w,
    session: { day: null, prev: null, next: null },
    unit,
    hero: { net: 0, gross: 0, costs: [], trades: 0, wins: 0, sessions: 0, daysWon: 0, daysLost: 0, open: 0, basis: null, shape: shapeOf([]) },
    tiles: [],
    gauges: [],
    breakdowns: [],
    arms: [],
    notes: [],
  };
}
