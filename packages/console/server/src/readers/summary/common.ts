import type { ModuleSummarySlice, SummaryArmRow, SummaryBreakdown, SummaryCost, SummaryHero, TradingMode } from "@console/shared";
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
}

export type SummaryReader = (config: ConsoleConfig, q: SummaryQuery) => ModuleSummarySlice;

/** One settled trade, in the module's own money layout (per-row TradeMoney). */
export interface SummaryTrade {
  /** The ET session the trade is booked to (its close/settle session). */
  session: string;
  arm: string;
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
export function heroOf(trades: readonly SummaryTrade[], chargesSlippage: boolean, open: number): SummaryHero {
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
    hero: { net: 0, gross: 0, costs: [], trades: 0, wins: 0, sessions: 0, daysWon: 0, daysLost: 0, open: 0, basis: null },
    tiles: [],
    gauges: [],
    breakdowns: [],
    arms: [],
    notes: [],
  };
}
