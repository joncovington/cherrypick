import path from "node:path";
import type { MeicPayload, MeicTradeRow, MeicSummaryRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

const TRADE_LIMIT = 50;

export function readMeic(config: ConsoleConfig, mode: TradingMode): MeicPayload {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);

  const trades = withReadOnlyDb<MeicTradeRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT id, trade_date, entry_time, symbol, put_strike, call_strike, wing_width,
                net_credit, quantity, status, pnl, fees, exit_reason, iv_rank_at_entry
           FROM ic_trades ORDER BY id DESC LIMIT ${TRADE_LIMIT}`,
      )
      .all()
      .map((r: Record<string, unknown>) => ({
        mode,
        id: num(r["id"]) ?? 0,
        tradeDate: str(r["trade_date"]) ?? "",
        entryTime: str(r["entry_time"]),
        symbol: str(r["symbol"]) ?? "",
        putStrike: num(r["put_strike"]),
        callStrike: num(r["call_strike"]),
        wingWidth: num(r["wing_width"]),
        netCredit: num(r["net_credit"]),
        quantity: num(r["quantity"]),
        status: str(r["status"]) ?? "",
        pnl: num(r["pnl"]),
        fees: num(r["fees"]),
        exitReason: str(r["exit_reason"]),
        ivRankAtEntry: num(r["iv_rank_at_entry"]),
      })),
  );

  const summaries = withReadOnlyDb<MeicSummaryRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT summary_date, symbol, total_entries, entries_filled, entries_stopped,
                net_pnl, win_rate_pct
           FROM daily_summary ORDER BY summary_date DESC LIMIT 20`,
      )
      .all()
      .map((r: Record<string, unknown>) => ({
        mode,
        summaryDate: str(r["summary_date"]) ?? "",
        symbol: str(r["symbol"]),
        totalEntries: num(r["total_entries"]),
        entriesFilled: num(r["entries_filled"]),
        entriesStopped: num(r["entries_stopped"]),
        netPnl: num(r["net_pnl"]),
        winRatePct: num(r["win_rate_pct"]),
      })),
  );

  return { mode, trades, summaries };
}

const RESOLVED = "status NOT IN ('cancelled','pending','partial_entry')";

export interface MeicBreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

export interface MeicDeepAnalytics {
  mode: TradingMode;
  /** Per trade date: gross, fees, net, count — the calendar heatmap's cells. */
  calendar: Array<{ date: string; net: number; trades: number }>;
  nlv: Array<{ date: string; nlv: number }>;
  byDelta: MeicBreakdownRow[];
  byWing: MeicBreakdownRow[];
  bySymbol: MeicBreakdownRow[];
  byWeekday: MeicBreakdownRow[];
}

export function readMeicDeepAnalytics(config: ConsoleConfig, mode: TradingMode): MeicDeepAnalytics {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicDeepAnalytics = { mode, calendar: [], nlv: [], byDelta: [], byWing: [], bySymbol: [], byWeekday: [] };
  return withReadOnlyDb<MeicDeepAnalytics>(dbPath, empty, (db) => {
    const calendar = db
      .prepare<[], Record<string, unknown>>(
        `SELECT trade_date, SUM(pnl) - SUM(COALESCE(fees, 0)) AS net, COUNT(*) AS trades
           FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL GROUP BY trade_date ORDER BY trade_date`,
      )
      .all()
      .map((r) => ({ date: String(r["trade_date"]), net: Number(r["net"]), trades: Number(r["trades"]) }));

    const nlv = db
      .prepare<[], Record<string, unknown>>(
        `SELECT summary_date, closing_nlv FROM daily_summary WHERE closing_nlv IS NOT NULL ORDER BY summary_date`,
      )
      .all()
      .map((r) => ({ date: String(r["summary_date"]), nlv: Number(r["closing_nlv"]) }));

    // Signal breakdowns, MEIC-dashboard rules: pnl IS NOT NULL, avg net = pnl − fees.
    const breakdown = (bucketExpr: string): MeicBreakdownRow[] =>
      db
        .prepare<[], Record<string, unknown>>(
          `SELECT ${bucketExpr} AS bucket, COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
                  SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                  AVG(pnl - COALESCE(fees, 0)) AS avg_net
             FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL
            GROUP BY bucket ORDER BY bucket`,
        )
        .all()
        .map((r) => {
          const trades = Number(r["trades"]);
          return {
            bucket: String(r["bucket"] ?? "?"),
            trades,
            sessions: Number(r["sessions"]),
            winPct: trades > 0 ? (Number(r["wins"]) / trades) * 100 : null,
            avgNet: r["avg_net"] === null ? null : Number(r["avg_net"]),
          };
        });

    return {
      mode,
      calendar,
      nlv,
      byDelta: breakdown(
        `CASE WHEN ABS(COALESCE(call_delta_at_entry, 0)) < 0.10 THEN '<0.10'
              WHEN ABS(call_delta_at_entry) < 0.15 THEN '0.10-0.15'
              WHEN ABS(call_delta_at_entry) < 0.20 THEN '0.15-0.20'
              ELSE '>=0.20' END`,
      ),
      byWing: breakdown(`CAST(CAST(wing_width AS INTEGER) AS TEXT) || '-wide'`),
      bySymbol: breakdown("symbol"),
      byWeekday: breakdown(
        `CASE CAST(strftime('%w', trade_date) AS INTEGER)
              WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue' WHEN 3 THEN 'Wed'
              WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri' ELSE 'Sat' END`,
      ),
    };
  });
}

export interface MeicAnalytics {
  mode: TradingMode;
  /** TODAY / WEEK / MONTH / YEAR / ALL, MEIC-dashboard rules: net = SUM(pnl); win = pnl − fees > 0. */
  periods: Array<{ label: string; net: number; trades: number; wins: number; losses: number }>;
  exitReasons: Array<{ reason: string; count: number }>;
  feeDrag: { grossCredit: number; fees: number; netPnl: number; dragPct: number | null };
}

export function readMeicAnalytics(config: ConsoleConfig, mode: TradingMode): MeicAnalytics {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicAnalytics = {
    mode,
    periods: [],
    exitReasons: [],
    feeDrag: { grossCredit: 0, fees: 0, netPnl: 0, dragPct: null },
  };
  return withReadOnlyDb<MeicAnalytics>(dbPath, empty, (db) => {
    // ET-anchored period starts, matching the MEIC dashboard.
    const nowEt = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    const today = iso(new Date(Date.UTC(nowEt.getFullYear(), nowEt.getMonth(), nowEt.getDate())));
    const monday = new Date(Date.UTC(nowEt.getFullYear(), nowEt.getMonth(), nowEt.getDate()));
    monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
    const starts: Array<[string, string | null]> = [
      ["today", today],
      ["week", iso(monday)],
      ["month", `${today.slice(0, 8)}01`],
      ["year", `${today.slice(0, 4)}-01-01`],
      ["all", null],
    ];
    const periodStmt = db.prepare<[string], Record<string, unknown>>(
      `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS trades,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0 THEN 1 ELSE 0 END) AS losses
         FROM ic_trades WHERE ${RESOLVED} AND trade_date >= ?`,
    );
    const allStmt = db.prepare<[], Record<string, unknown>>(
      `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS trades,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0 THEN 1 ELSE 0 END) AS losses
         FROM ic_trades WHERE ${RESOLVED}`,
    );
    const periods = starts.map(([label, start]) => {
      const r = (start === null ? allStmt.get() : periodStmt.get(start)) ?? {};
      return {
        label,
        net: Number(r["net"] ?? 0),
        trades: Number(r["trades"] ?? 0),
        wins: Number(r["wins"] ?? 0),
        losses: Number(r["losses"] ?? 0),
      };
    });

    const exitReasons = db
      .prepare<[], Record<string, unknown>>(
        `SELECT COALESCE(exit_reason, 'open') AS reason, COUNT(*) AS count
           FROM ic_trades WHERE ${RESOLVED} GROUP BY COALESCE(exit_reason, 'open') ORDER BY count DESC`,
      )
      .all()
      .map((r) => ({ reason: String(r["reason"]), count: Number(r["count"]) }));

    const fd = db
      .prepare<[], Record<string, unknown>>(
        `SELECT COALESCE(SUM(net_credit * COALESCE(quantity, 1) * 100), 0) AS gross,
                COALESCE(SUM(fees), 0) AS fees, COALESCE(SUM(pnl), 0) AS net
           FROM ic_trades WHERE ${RESOLVED}`,
      )
      .get() ?? {};
    const gross = Number(fd["gross"] ?? 0);
    const fees = Number(fd["fees"] ?? 0);
    return {
      mode,
      periods,
      exitReasons,
      feeDrag: {
        grossCredit: gross,
        fees,
        netPnl: Number(fd["net"] ?? 0),
        dragPct: gross > 0 ? (fees / gross) * 100 : null,
      },
    };
  });
}
