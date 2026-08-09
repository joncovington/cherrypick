import path from "node:path";
import type { FliesPayload, FliesBookRow, FliesPositionRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

export interface FliesFilter {
  arm: string | null;
  date: string | null;
}

function filterSql(filter: FliesFilter): { where: string; params: string[] } {
  const clauses: string[] = [];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  if (filter.date !== null) {
    clauses.push("trade_date = ?");
    params.push(filter.date);
  }
  return { where: clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "", params };
}

export function readFlies(config: ConsoleConfig, mode: TradingMode, filter: FliesFilter): FliesPayload {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const { where, params } = filterSql(filter);

  const books = withReadOnlyDb<FliesBookRow[]>(dbPath, [], (db) =>
    db
      .prepare<string[], Record<string, unknown>>(
        `SELECT book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
                net_cash, floor_holds, band_low, band_high, pnl, status
           FROM fly_books ${where} ORDER BY id DESC LIMIT 30`,
      )
      .all(...params)
      .map((r: Record<string, unknown>) => ({
        mode,
        bookId: str(r["book_id"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        arm: str(r["arm"]),
        symbol: str(r["symbol"]) ?? "",
        creditCollected: num(r["credit_collected"]),
        debitsPaid: num(r["debits_paid"]),
        fees: num(r["fees"]),
        netCash: num(r["net_cash"]),
        floorHolds: r["floor_holds"] === null ? null : r["floor_holds"] === 1,
        bandLow: num(r["band_low"]),
        bandHigh: num(r["band_high"]),
        pnl: num(r["pnl"]),
        status: str(r["status"]) ?? "",
      })),
  );

  const positions = withReadOnlyDb<FliesPositionRow[]>(dbPath, [], (db) =>
    db
      .prepare<string[], Record<string, unknown>>(
        `SELECT position_id, trade_date, symbol, arm, entry_mode, kind, side, center, wing_width,
                quantity, net, floor_dollars, risk_free, status, pnl, entry_time
           FROM fly_positions ${where} ORDER BY id DESC LIMIT 50`,
      )
      .all(...params)
      .map((r: Record<string, unknown>) => ({
        mode,
        positionId: str(r["position_id"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        arm: str(r["arm"]),
        entryMode: str(r["entry_mode"]),
        kind: str(r["kind"]),
        side: str(r["side"]),
        center: num(r["center"]),
        wingWidth: num(r["wing_width"]),
        quantity: num(r["quantity"]),
        net: num(r["net"]),
        floorDollars: num(r["floor_dollars"]),
        riskFree: r["risk_free"] === 1,
        status: str(r["status"]) ?? "",
        pnl: num(r["pnl"]),
        entryTime: str(r["entry_time"]),
      })),
  );

  return { mode, books, positions };
}

import { payoffCurve, stateAt, bookPnl, type FlyPosition, type FlyRow, type PayoffCurve } from "../analytics/fliesPayoff.js";

export interface FliesForest {
  mode: TradingMode;
  tradeDate: string | null;
  /** One curve per arm active on the day. */
  arms: Array<{ arm: string; curve: PayoffCurve }>;
  /** The day's settlement print when the session has settled; null intraday. */
  settlement: { price: number; source: string | null } | null;
  /** Last recorded intraday tick for the day, for the settled-vs-close note. */
  lastTickSpot: number | null;
}

/** Distinct arms and trade dates, for the page's filter selects. */
export function readFliesMeta(config: ConsoleConfig, mode: TradingMode): { arms: string[]; dates: string[] } {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<{ arms: string[]; dates: string[] }>(dbPath, { arms: [], dates: [] }, (db) => ({
    arms: db
      .prepare<[], { arm: string }>("SELECT DISTINCT arm FROM fly_positions WHERE arm IS NOT NULL ORDER BY arm")
      .all()
      .map((r) => r.arm),
    dates: db
      .prepare<[], { d: string }>("SELECT DISTINCT trade_date AS d FROM fly_positions ORDER BY trade_date DESC")
      .all()
      .map((r) => r.d),
  }));
}

/** The profit forest: per-arm book payoff curves for the latest (or given) day. */
export function readFliesForest(
  config: ConsoleConfig,
  mode: TradingMode,
  day: string | null,
  arm: string | null,
): FliesForest {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesForest = { mode, tradeDate: null, arms: [], settlement: null, lastTickSpot: null };
  return withReadOnlyDb<FliesForest>(dbPath, empty, (db) => {
    const tradeDate =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions").get()?.d ?? null;
    if (tradeDate === null) return empty;

    const settleRow = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT settlement_price, settlement_source FROM fly_books
          WHERE trade_date = ? AND settlement_price IS NOT NULL LIMIT 1`,
      )
      .get(tradeDate);
    const settlement =
      typeof settleRow?.["settlement_price"] === "number"
        ? {
            price: settleRow["settlement_price"],
            source:
              typeof settleRow["settlement_source"] === "string" && settleRow["settlement_source"] !== ""
                ? settleRow["settlement_source"]
                : null,
          }
        : null;
    const lastTick = db
      .prepare<[string], { spot: number | null }>(
        "SELECT underlying_price AS spot FROM fly_iterations WHERE trade_date = ? AND underlying_price IS NOT NULL ORDER BY iteration_ts DESC LIMIT 1",
      )
      .get(tradeDate);
    const armClause = arm !== null ? " AND arm = ?" : "";
    const params: string[] = arm !== null ? [tradeDate, arm] : [tradeDate];
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, kind, side, center, wing_width, far_width, net, quantity, fees, status
           FROM fly_positions WHERE trade_date = ? AND status != 'voided' AND void_reason IS NULL${armClause}`,
      )
      .all(...params);
    const byArm = new Map<string, FlyPosition[]>();
    for (const r of rows) {
      const arm = String(r["arm"] ?? "?");
      let list = byArm.get(arm);
      if (list === undefined) {
        list = [];
        byArm.set(arm, list);
      }
      list.push({
        kind: String(r["kind"] ?? "fly"),
        side: String(r["side"] ?? "put"),
        center: Number(r["center"]),
        wingWidth: Number(r["wing_width"]),
        farWidth: typeof r["far_width"] === "number" ? r["far_width"] : null,
        net: Number(r["net"] ?? 0),
        quantity: Number(r["quantity"] ?? 1),
        fees: Number(r["fees"] ?? 0),
        status: r["status"] === null ? null : String(r["status"]),
      });
    }
    return {
      mode,
      tradeDate,
      arms: [...byArm.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([arm, positions]) => ({ arm, curve: payoffCurve(positions) })),
      settlement,
      lastTickSpot: lastTick?.spot ?? null,
    };
  });
}

export interface TimelineTick {
  ts: string;
  spot: number | null;
  centers: Record<string, number>;
  settleNow: Record<string, number>;
}

export interface TimelineEvent {
  kind: "entry" | "completion";
  ts: string;
  arm: string;
  center: number | null;
  spot: number | null;
  structure: string;
}

export interface TimelineSpan {
  arm: string;
  center: number | null;
  from: string;
  to: string;
  latencyMin: number | null;
}

export interface FliesTimeline {
  mode: TradingMode;
  date: string | null;
  arms: string[];
  ticks: TimelineTick[];
  events: TimelineEvent[];
  spans: TimelineSpan[];
  waiting: Array<{ arm: string; center: number | null; from: string }>;
  feed: Array<{ ts: string; status: string }>;
  feedSummary: { total: number; ok: number; refusals: Record<string, number> };
}

/**
 * Port of analytics.session_timeline: the session along a TIME axis. ticks =
 * spot + each arm's wanted centre per iteration with the book replayed at each
 * tick via stateAt (an expiry payoff at a live spot, NOT a mark); events =
 * entries/completions; spans = leg-in → completion; waiting = credit spreads
 * still carrying full defined risk; feed = per-tick snapshot statuses so a
 * silence can name its reason.
 */
export function readFliesTimeline(config: ConsoleConfig, mode: TradingMode, day: string | null): FliesTimeline {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesTimeline = {
    mode,
    date: null,
    arms: [],
    ticks: [],
    events: [],
    spans: [],
    waiting: [],
    feed: [],
    feedSummary: { total: 0, ok: 0, refusals: {} },
  };
  return withReadOnlyDb<FliesTimeline>(dbPath, empty, (db) => {
    const date =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_iterations").get()?.d ?? null;
    if (date === null) return empty;

    interface TimelineRow extends FlyRow {
      arm: string;
      spotAtCompletion: number | null;
      underlyingAtEntry: number | null;
      latencyMin: number | null;
    }
    const rows: TimelineRow[] = db
      .prepare<[string], Record<string, unknown>>("SELECT * FROM fly_positions WHERE trade_date = ? ORDER BY entry_time")
      .all(date)
      .map((r): TimelineRow => ({
        kind: String(r["kind"] ?? "fly"),
        side: String(r["side"] ?? "put"),
        center: Number(r["center"]),
        wingWidth: Number(r["wing_width"]),
        farWidth: typeof r["far_width"] === "number" ? r["far_width"] : null,
        net: Number(r["net"] ?? 0),
        quantity: Number(r["quantity"] ?? 1),
        fees: Number(r["fees"] ?? 0),
        status: r["status"] === null ? null : String(r["status"]),
        symbol: String(r["symbol"] ?? "XSP"),
        entryTime: r["entry_time"] === null ? null : String(r["entry_time"]),
        completedAt: r["completed_at"] === null ? null : String(r["completed_at"]),
        entryMode: r["entry_mode"] === null ? null : String(r["entry_mode"]),
        credit: typeof r["credit"] === "number" ? r["credit"] : null,
        debit: typeof r["debit"] === "number" ? r["debit"] : null,
        arm: String(r["arm"] ?? "?"),
        spotAtCompletion: typeof r["spot_at_completion"] === "number" ? r["spot_at_completion"] : null,
        underlyingAtEntry: typeof r["underlying_at_entry"] === "number" ? r["underlying_at_entry"] : null,
        latencyMin: typeof r["completion_latency_min"] === "number" ? r["completion_latency_min"] : null,
      }));

    const iterations = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT iteration_ts, arm, center, underlying_price FROM fly_iterations WHERE trade_date = ? ORDER BY iteration_ts",
      )
      .all(date);

    const arms = [...new Set([...rows.map((r) => r.arm), ...iterations.map((r) => String(r["arm"] ?? ""))])]
      .filter((a) => a !== "")
      .sort();
    const byArm = new Map<string, FlyRow[]>();
    for (const r of rows) {
      let list = byArm.get(r.arm);
      if (list === undefined) {
        list = [];
        byArm.set(r.arm, list);
      }
      list.push(r);
    }

    const grouped = new Map<string, Array<Record<string, unknown>>>();
    for (const it of iterations) {
      const ts = String(it["iteration_ts"]);
      let list = grouped.get(ts);
      if (list === undefined) {
        list = [];
        grouped.set(ts, list);
      }
      list.push(it);
    }

    const ticks: TimelineTick[] = [];
    for (const ts of [...grouped.keys()].sort()) {
      const entries = grouped.get(ts)!;
      const spot = entries.map((e) => e["underlying_price"]).find((v): v is number => typeof v === "number") ?? null;
      const centers: Record<string, number> = {};
      for (const e of entries) {
        if (typeof e["center"] === "number") centers[String(e["arm"])] = e["center"];
      }
      const settleNow: Record<string, number> = {};
      if (spot !== null) {
        for (const arm of arms) {
          const states = (byArm.get(arm) ?? [])
            .map((p) => stateAt(p, ts))
            .filter((s): s is FlyPosition => s !== null);
          if (states.length > 0) settleNow[arm] = Math.round(bookPnl(states, spot) * 100) / 100;
        }
      }
      ticks.push({ ts, spot, centers, settleNow });
    }

    const events: TimelineEvent[] = [];
    const spans: TimelineSpan[] = [];
    const waiting: FliesTimeline["waiting"] = [];
    for (const r of rows) {
      if (r.entryTime !== null) {
        events.push({
          kind: "entry",
          ts: r.entryTime,
          arm: r.arm,
          center: r.center,
          spot: r.underlyingAtEntry,
          structure: r.entryMode === "legged" ? `short ${r.side}` : r.entryMode ?? "fly",
        });
      }
      if (r.completedAt !== null) {
        events.push({
          kind: "completion",
          ts: r.completedAt,
          arm: r.arm,
          center: r.center,
          spot: r.spotAtCompletion,
          structure: r.kind === "iron_fly" ? "iron fly" : "fly",
        });
        if (r.entryTime !== null) {
          spans.push({ arm: r.arm, center: r.center, from: r.entryTime, to: r.completedAt, latencyMin: r.latencyMin });
        }
      } else if (r.entryMode === "legged" && r.entryTime !== null) {
        waiting.push({ arm: r.arm, center: r.center, from: r.entryTime });
      }
    }
    events.sort((a, b) => a.ts.localeCompare(b.ts));

    const feedRows = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT iteration_ts, status FROM fly_snapshots WHERE trade_date = ? ORDER BY iteration_ts",
      )
      .all(date)
      .map((r) => ({ ts: String(r["iteration_ts"]), status: String(r["status"] ?? "?") }));
    const refusals: Record<string, number> = {};
    let ok = 0;
    for (const f of feedRows) {
      if (f.status === "ok") ok += 1;
      else refusals[f.status] = (refusals[f.status] ?? 0) + 1;
    }

    return {
      mode,
      date,
      arms,
      ticks,
      events,
      spans,
      waiting,
      feed: feedRows,
      feedSummary: { total: feedRows.length, ok, refusals },
    };
  });
}

export interface JournalRow {
  arm: string;
  mode: string;
  reason: string;
  accepted: boolean;
  firstSeen: string | null;
  lastSeen: string | null;
  occurrences: number;
  centerLast: number | null;
  detail: string | null;
}

/** The day's decisions, newest run first — already collapsed at write time, so a plain read. */
export function readFliesJournal(config: ConsoleConfig, mode: TradingMode, day: string | null, arm: string | null): { date: string | null; rows: JournalRow[] } {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<{ date: string | null; rows: JournalRow[] }>(dbPath, { date: null, rows: [] }, (db) => {
    const date =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_decisions").get()?.d ?? null;
    if (date === null) return { date: null, rows: [] };
    const clause = arm !== null ? " AND arm = ?" : "";
    const params: string[] = arm !== null ? [date, arm] : [date];
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, mode, reason, accepted, first_seen, last_seen, occurrences, center_last, detail
           FROM fly_decisions WHERE trade_date = ?${clause} ORDER BY id DESC`,
      )
      .all(...params)
      .map((r) => ({
        arm: String(r["arm"] ?? "?"),
        mode: String(r["mode"] ?? "?"),
        reason: String(r["reason"] ?? ""),
        accepted: r["accepted"] === 1,
        firstSeen: str(r["first_seen"]),
        lastSeen: str(r["last_seen"]),
        occurrences: Number(r["occurrences"] ?? 1),
        centerLast: num(r["center_last"]),
        detail: str(r["detail"]),
      }));
    return { date, rows };
  });
}

// ---- history / performance (ports of analytics.py's read layer) ----

/** Settled, non-void — the shared WHERE every history read builds on. */
const SETTLED = "status = 'settled' AND void_reason IS NULL";

export interface FliesSummary {
  trades: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  wins: number;
  losses: number;
  winRatePct: number | null;
  avgPnl: number | null;
  feeDragPct: number | null;
  profitFactor: number | null;
}

interface PnlRow {
  gross: number;
  fees: number;
  pnl: number;
}

function summarize(rows: PnlRow[]): FliesSummary {
  const gross = rows.reduce((s, r) => s + r.gross, 0);
  const fees = rows.reduce((s, r) => s + r.fees, 0);
  const nets = rows.map((r) => r.pnl);
  const wins = nets.filter((n) => n > 0);
  const losses = nets.filter((n) => n < 0);
  const totalWin = wins.reduce((s, n) => s + n, 0);
  const totalLoss = Math.abs(losses.reduce((s, n) => s + n, 0));
  const net = nets.reduce((s, n) => s + n, 0);
  return {
    trades: rows.length,
    grossPnl: gross,
    fees,
    netPnl: net,
    wins: wins.length,
    losses: losses.length,
    winRatePct: wins.length + losses.length > 0 ? (wins.length / (wins.length + losses.length)) * 100 : null,
    avgPnl: nets.length > 0 ? net / nets.length : null,
    feeDragPct: gross > 0 ? (fees / gross) * 100 : null,
    profitFactor: totalLoss > 0 ? totalWin / totalLoss : null,
  };
}

function pnlRows(db: import("better-sqlite3").Database, where: string, params: string[]): Array<Record<string, unknown>> {
  return db
    .prepare<string[], Record<string, unknown>>(
      `SELECT trade_date, arm, entry_mode, entry_window, COALESCE(gross_pnl, 0) AS gross,
              COALESCE(fees, 0) AS fees, COALESCE(pnl, 0) AS pnl
         FROM fly_positions WHERE ${where}`,
    )
    .all(...params);
}

const toPnl = (r: Record<string, unknown>): PnlRow => ({ gross: Number(r["gross"]), fees: Number(r["fees"]), pnl: Number(r["pnl"]) });

function groupSummaries<T extends string>(rows: Array<Record<string, unknown>>, key: string, label: T): Array<Record<T, string> & FliesSummary> {
  const grouped = new Map<string, PnlRow[]>();
  for (const r of rows) {
    const k = String(r[key] ?? "unknown");
    let list = grouped.get(k);
    if (list === undefined) {
      list = [];
      grouped.set(k, list);
    }
    list.push(toPnl(r));
  }
  return [...grouped.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([k, rs]) => ({ [label]: k, ...summarize(rs) }) as Record<T, string> & FliesSummary);
}

export interface FliesHistory {
  mode: TradingMode;
  /** Legged-only, per the reference: the arms differ by centring/timing/width, never by entry mode. */
  byArm: Array<{ arm: string } & FliesSummary>;
  byEntryMode: Array<{ entryMode: string } & FliesSummary>;
  byEntryWindow: Array<{ window: string } & FliesSummary>;
  feeDrag: Array<{ arm: string } & FliesSummary>;
  dailyPnl: Array<{ date: string; trades: number; netPnl: number }>;
  tradeLog: Array<{
    tradeDate: string;
    symbol: string;
    arm: string | null;
    entryMode: string | null;
    kind: string | null;
    side: string | null;
    center: number | null;
    window: string | null;
    net: number | null;
    fees: number | null;
    pnl: number | null;
    latencyMin: number | null;
    pinned: boolean;
  }>;
}

export function readFliesHistory(config: ConsoleConfig, mode: TradingMode): FliesHistory {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesHistory = { mode, byArm: [], byEntryMode: [], byEntryWindow: [], feeDrag: [], dailyPnl: [], tradeLog: [] };
  return withReadOnlyDb<FliesHistory>(dbPath, empty, (db) => {
    const legged = pnlRows(db, `${SETTLED} AND entry_mode = 'legged'`, []);
    const all = pnlRows(db, SETTLED, []);
    const byArm = groupSummaries(legged, "arm", "arm").sort((a, b) => b.netPnl - a.netPnl);

    const dailyMap = new Map<string, PnlRow[]>();
    for (const r of all) {
      const d = String(r["trade_date"]);
      let list = dailyMap.get(d);
      if (list === undefined) {
        list = [];
        dailyMap.set(d, list);
      }
      list.push(toPnl(r));
    }
    const dailyPnl = [...dailyMap.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, rs]) => ({ date, trades: rs.length, netPnl: rs.reduce((s, x) => s + x.pnl, 0) }));

    const tradeLog = db
      .prepare<[], Record<string, unknown>>(
        `SELECT trade_date, symbol, arm, entry_mode, kind, side, center, entry_window, net, fees, pnl,
                completion_latency_min, pinned
           FROM fly_positions WHERE ${SETTLED}
          ORDER BY trade_date DESC, entry_time DESC LIMIT 500`,
      )
      .all()
      .map((r) => ({
        tradeDate: String(r["trade_date"]),
        symbol: String(r["symbol"] ?? ""),
        arm: str(r["arm"]),
        entryMode: str(r["entry_mode"]),
        kind: str(r["kind"]),
        side: str(r["side"]),
        center: num(r["center"]),
        window: str(r["entry_window"]),
        net: num(r["net"]),
        fees: num(r["fees"]),
        pnl: num(r["pnl"]),
        latencyMin: num(r["completion_latency_min"]),
        pinned: r["pinned"] === 1,
      }));

    return {
      mode,
      byArm,
      byEntryMode: groupSummaries(all, "entry_mode", "entryMode"),
      byEntryWindow: groupSummaries(all, "entry_window", "window"),
      feeDrag: byArm,
      dailyPnl,
      tradeLog,
    };
  });
}

export interface FliesPerformance {
  mode: TradingMode;
  tiles: FliesSummary & { completionRatePct: number | null };
  series: Array<{ bucket: string; netPnl: number; cumulative: number }>;
  completion: {
    leggedEntries: number;
    completed: number;
    completionRatePct: number | null;
    neverOffered: number;
    bufferBlocked: number;
    floorBlocked: number;
    unknown: number;
    medianLatencyMin: number | null;
    minLatencyMin: number | null;
    maxLatencyMin: number | null;
    medianSpotMove: number | null;
  };
  completionTrend: Array<{ day: string; legged: number; completed: number; ratePct: number | null }>;
  liveVsPaper: {
    arm: string;
    live: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    paper: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    completionGapPct: number | null;
    abort: { minLiveEntries: number; gapLimitPct: number; armed: boolean; triggered: boolean };
  } | null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1 ? ordered[mid]! : (ordered[mid - 1]! + ordered[mid]!) / 2;
}

/** Weekly buckets are Monday-anchored (SQLite's %W would split a trading week). */
function bucketKey(tradeDate: string, granularity: string): string {
  if (granularity === "monthly") return tradeDate.slice(0, 7);
  if (granularity === "weekly") {
    const d = new Date(tradeDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  return tradeDate;
}

const ABORT_MIN_LIVE_ENTRIES = 30;
const ABORT_COMPLETION_GAP = 0.15;

export function readFliesPerformance(config: ConsoleConfig, mode: TradingMode, granularity: string): FliesPerformance {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesPerformance = {
    mode,
    tiles: { ...summarize([]), completionRatePct: null },
    series: [],
    completion: {
      leggedEntries: 0, completed: 0, completionRatePct: null, neverOffered: 0, bufferBlocked: 0,
      floorBlocked: 0, unknown: 0, medianLatencyMin: null, minLatencyMin: null, maxLatencyMin: null, medianSpotMove: null,
    },
    completionTrend: [],
    liveVsPaper: null,
  };
  const result = withReadOnlyDb<FliesPerformance>(dbPath, empty, (db) => {
    const all = pnlRows(db, SETTLED, []);
    const tilesBase = summarize(all.map(toPnl));

    const buckets = new Map<string, PnlRow[]>();
    for (const r of all) {
      const k = bucketKey(String(r["trade_date"]), granularity);
      let list = buckets.get(k);
      if (list === undefined) {
        list = [];
        buckets.set(k, list);
      }
      list.push(toPnl(r));
    }
    let cumulative = 0;
    const series = [...buckets.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([bucket, rs]) => {
        const net = rs.reduce((s, x) => s + x.pnl, 0);
        cumulative += net;
        return { bucket, netPnl: net, cumulative };
      });

    // completion_stats, legged: the counterfactual's floor split reads the
    // decisions journal (the engine's own recorded reason), never recomputed.
    const modeRows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT position_id, kind, credit, best_completing_debit, completion_latency_min,
                underlying_at_entry, spot_at_completion
           FROM fly_positions WHERE entry_mode = 'legged'`,
      )
      .all();
    const floorGated = new Set(
      db
        .prepare<[], { position_id: string | null }>(
          "SELECT DISTINCT position_id FROM fly_decisions WHERE mode = 'completion' AND reason = 'floor_below_minimum_after_fees'",
        )
        .all()
        .map((r) => r.position_id)
        .filter((p): p is string => p !== null),
    );
    const completed = modeRows.filter((r) => r["kind"] === "fly");
    const missed = modeRows.filter((r) => r["kind"] !== "fly");
    let neverOffered = 0;
    let bufferBlocked = 0;
    let floorBlocked = 0;
    let unknown = 0;
    for (const r of missed) {
      const best = num(r["best_completing_debit"]);
      const target = num(r["credit"]);
      if (best === null || target === null) unknown += 1;
      else if (!(best < target)) neverOffered += 1;
      else if (floorGated.has(String(r["position_id"]))) floorBlocked += 1;
      else bufferBlocked += 1;
    }
    const latencies = completed.map((r) => num(r["completion_latency_min"])).filter((v): v is number => v !== null);
    const moves = completed
      .map((r) => {
        const a = num(r["spot_at_completion"]);
        const b = num(r["underlying_at_entry"]);
        return a !== null && b !== null ? Math.abs(a - b) : null;
      })
      .filter((v): v is number => v !== null);

    const trend = db
      .prepare<[], Record<string, unknown>>(
        `SELECT trade_date, COUNT(*) AS legged, SUM(CASE WHEN kind = 'fly' THEN 1 ELSE 0 END) AS completed
           FROM fly_positions WHERE entry_mode = 'legged' GROUP BY trade_date ORDER BY trade_date`,
      )
      .all()
      .map((r) => {
        const legged = Number(r["legged"]);
        const done = Number(r["completed"]);
        return { day: String(r["trade_date"]), legged, completed: done, ratePct: legged > 0 ? (done / legged) * 100 : null };
      });

    return {
      mode,
      tiles: {
        ...tilesBase,
        completionRatePct: modeRows.length > 0 ? (completed.length / modeRows.length) * 100 : null,
      },
      series,
      completion: {
        leggedEntries: modeRows.length,
        completed: completed.length,
        completionRatePct: modeRows.length > 0 ? (completed.length / modeRows.length) * 100 : null,
        neverOffered,
        bufferBlocked,
        floorBlocked,
        unknown,
        medianLatencyMin: median(latencies),
        minLatencyMin: latencies.length > 0 ? Math.min(...latencies) : null,
        maxLatencyMin: latencies.length > 0 ? Math.max(...latencies) : null,
        medianSpotMove: median(moves),
      },
      completionTrend: trend,
      liveVsPaper: null,
    };
  });

  // live vs paper: CONTEMPORANEOUS — paper restricted to the live arm's sessions.
  result.liveVsPaper = withReadOnlyDb(path.join(config.paths.fliesDir, "live_trades.db"), null, (liveDb) => {
    const days = liveDb
      .prepare<[], { d: string }>(
        "SELECT DISTINCT trade_date AS d FROM fly_positions WHERE arm = 'gex' AND entry_mode = 'legged' AND status != 'cancelled' ORDER BY trade_date",
      )
      .all()
      .map((r) => r.d);
    if (days.length === 0) return null;
    const marks = days.map(() => "?").join(",");
    const side = (db: import("better-sqlite3").Database) => {
      const rows = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT kind, credit, completion_latency_min FROM fly_positions
            WHERE arm = 'gex' AND entry_mode = 'legged' AND status != 'cancelled' AND trade_date IN (${marks})`,
        )
        .all(...days);
      const done = rows.filter((r) => r["kind"] === "fly");
      const lat = done.map((r) => num(r["completion_latency_min"])).filter((v): v is number => v !== null && v !== 0);
      const credits = rows.map((r) => num(r["credit"])).filter((v): v is number => v !== null);
      return {
        sessions: days.length,
        entries: rows.length,
        completed: done.length,
        completionRatePct: rows.length > 0 ? (done.length / rows.length) * 100 : null,
        medianLatencyMin: median(lat),
        avgCredit: credits.length > 0 ? credits.reduce((s, v) => s + v, 0) / credits.length : null,
      };
    };
    const live = side(liveDb);
    const paper = withReadOnlyDb(path.join(config.paths.fliesDir, "paper_trades.db"), side(liveDb), side);
    const gap =
      live.completionRatePct !== null && paper.completionRatePct !== null
        ? (paper.completionRatePct - live.completionRatePct) / 100
        : null;
    const armed = live.entries >= ABORT_MIN_LIVE_ENTRIES;
    return {
      arm: "gex",
      live,
      paper,
      completionGapPct: gap !== null ? gap * 100 : null,
      abort: {
        minLiveEntries: ABORT_MIN_LIVE_ENTRIES,
        gapLimitPct: ABORT_COMPLETION_GAP * 100,
        armed,
        triggered: armed && gap !== null && gap > ABORT_COMPLETION_GAP,
      },
    };
  });

  return result;
}

export interface FliesAnalytics {
  mode: TradingMode;
  /** Latest trade date's tiles, flies-dashboard shape. */
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
  };
  byArm: Array<{ arm: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  feeDrag: Array<{ arm: string; gross: number; fees: number; net: number; dragPct: number | null }>;
}

export function readFliesAnalytics(config: ConsoleConfig, mode: TradingMode, filter: FliesFilter): FliesAnalytics {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesAnalytics = {
    mode,
    today: { tradeDate: null, netPnl: 0, positions: 0, open: 0, riskFree: 0, completionPct: null, fees: 0 },
    byArm: [],
    feeDrag: [],
  };
  return withReadOnlyDb<FliesAnalytics>(dbPath, empty, (db) => {
    const latest = db
      .prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions")
      .get();
    const tradeDate = filter.date ?? latest?.d ?? null;
    const armClause = filter.arm !== null ? " AND arm = ?" : "";
    const armParams: string[] = filter.arm !== null ? [filter.arm] : [];

    let today = empty.today;
    if (tradeDate !== null) {
      const t = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS positions,
                  SUM(CASE WHEN status NOT IN ('settled','closed','voided') THEN 1 ELSE 0 END) AS open,
                  SUM(CASE WHEN risk_free = 1 THEN 1 ELSE 0 END) AS risk_free,
                  SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                  COALESCE(SUM(fees), 0) AS fees
             FROM fly_positions WHERE trade_date = ?${armClause}`,
        )
        .get(tradeDate, ...armParams) ?? {};
      const positions = Number(t["positions"] ?? 0);
      today = {
        tradeDate,
        netPnl: Number(t["net"] ?? 0),
        positions,
        open: Number(t["open"] ?? 0),
        riskFree: Number(t["risk_free"] ?? 0),
        completionPct: positions > 0 ? (Number(t["completed"] ?? 0) / positions) * 100 : null,
        fees: Number(t["fees"] ?? 0),
      };
    }

    const dateClause = filter.date !== null ? " AND trade_date = ?" : "";
    const dateParams: string[] = filter.date !== null ? [filter.date] : [];
    const armRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, COUNT(*) AS trades, COALESCE(SUM(pnl), 0) AS net,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) AS won,
                COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) AS lost
           FROM fly_positions WHERE pnl IS NOT NULL${dateClause}${armClause} GROUP BY arm ORDER BY net DESC`,
      )
      .all(...dateParams, ...armParams);
    const byArm = armRows.map((r) => {
      const trades = Number(r["trades"]);
      const wins = Number(r["wins"]);
      const losses = Number(r["losses"]);
      const lost = Number(r["lost"]);
      return {
        arm: String(r["arm"] ?? "?"),
        trades,
        net: Number(r["net"]),
        winPct: wins + losses > 0 ? (wins / (wins + losses)) * 100 : null,
        avg: trades > 0 ? Number(r["net"]) / trades : null,
        profitFactor: lost > 0 ? Number(r["won"]) / lost : null,
      };
    });

    const feeDrag = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, COALESCE(SUM(gross_pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees,
                COALESCE(SUM(pnl), 0) AS net
           FROM fly_positions WHERE pnl IS NOT NULL${dateClause}${armClause} GROUP BY arm ORDER BY arm`,
      )
      .all(...dateParams, ...armParams)
      .map((r) => {
        const gross = Number(r["gross"]);
        const fees = Number(r["fees"]);
        return {
          arm: String(r["arm"] ?? "?"),
          gross,
          fees,
          net: Number(r["net"]),
          dragPct: Math.abs(gross) > 0 ? (fees / Math.abs(gross)) * 100 : null,
        };
      });

    return { mode, today, byArm, feeDrag };
  });
}
