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
