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
