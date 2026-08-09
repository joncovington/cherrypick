import path from "node:path";
import type { FliesPayload, FliesBookRow, FliesPositionRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

export function readFlies(config: ConsoleConfig, mode: TradingMode): FliesPayload {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);

  const books = withReadOnlyDb<FliesBookRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
                net_cash, floor_holds, band_low, band_high, pnl, status
           FROM fly_books ORDER BY id DESC LIMIT 30`,
      )
      .all()
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
      .prepare<[], Record<string, unknown>>(
        `SELECT position_id, trade_date, symbol, kind, side, center, wing_width,
                quantity, net, status, pnl, entry_time
           FROM fly_positions ORDER BY id DESC LIMIT 50`,
      )
      .all()
      .map((r: Record<string, unknown>) => ({
        mode,
        positionId: str(r["position_id"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        kind: str(r["kind"]),
        side: str(r["side"]),
        center: num(r["center"]),
        wingWidth: num(r["wing_width"]),
        quantity: num(r["quantity"]),
        net: num(r["net"]),
        status: str(r["status"]) ?? "",
        pnl: num(r["pnl"]),
        entryTime: str(r["entry_time"]),
      })),
  );

  return { mode, books, positions };
}

import { payoffCurve, type FlyPosition, type PayoffCurve } from "../analytics/fliesPayoff.js";

export interface FliesForest {
  mode: TradingMode;
  tradeDate: string | null;
  /** One curve per arm active on the day. */
  arms: Array<{ arm: string; curve: PayoffCurve }>;
}

/** The profit forest: per-arm book payoff curves for the latest (or given) day. */
export function readFliesForest(config: ConsoleConfig, mode: TradingMode, day: string | null): FliesForest {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<FliesForest>(dbPath, { mode, tradeDate: null, arms: [] }, (db) => {
    const tradeDate =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions").get()?.d ?? null;
    if (tradeDate === null) return { mode, tradeDate: null, arms: [] };
    const rows = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT arm, kind, side, center, wing_width, far_width, net, quantity, fees, status
           FROM fly_positions WHERE trade_date = ? AND status != 'voided' AND void_reason IS NULL`,
      )
      .all(tradeDate);
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

export function readFliesAnalytics(config: ConsoleConfig, mode: TradingMode): FliesAnalytics {
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
    const tradeDate = latest?.d ?? null;

    let today = empty.today;
    if (tradeDate !== null) {
      const t = db
        .prepare<[string], Record<string, unknown>>(
          `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS positions,
                  SUM(CASE WHEN status NOT IN ('settled','closed','voided') THEN 1 ELSE 0 END) AS open,
                  SUM(CASE WHEN risk_free = 1 THEN 1 ELSE 0 END) AS risk_free,
                  SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                  COALESCE(SUM(fees), 0) AS fees
             FROM fly_positions WHERE trade_date = ?`,
        )
        .get(tradeDate) ?? {};
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

    const armRows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT arm, COUNT(*) AS trades, COALESCE(SUM(pnl), 0) AS net,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) AS won,
                COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) AS lost
           FROM fly_positions WHERE pnl IS NOT NULL GROUP BY arm ORDER BY net DESC`,
      )
      .all();
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
      .prepare<[], Record<string, unknown>>(
        `SELECT arm, COALESCE(SUM(gross_pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees,
                COALESCE(SUM(pnl), 0) AS net
           FROM fly_positions WHERE pnl IS NOT NULL GROUP BY arm ORDER BY arm`,
      )
      .all()
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
