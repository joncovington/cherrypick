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
