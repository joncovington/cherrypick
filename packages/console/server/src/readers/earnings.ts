import path from "node:path";
import type { EarningsPayload, EarningsTradeRow, EntryReviewRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

function readTrades(dbPath: string, mode: TradingMode): EarningsTradeRow[] {
  return withReadOnlyDb<EarningsTradeRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT order_id, symbol, strategy, expiration, entry_credit, pnl, quantity,
                opened_at, closed_at, profile
           FROM trades ORDER BY opened_at DESC LIMIT 50`,
      )
      .all()
      .map((r: Record<string, unknown>) => ({
        mode,
        orderId: String(r["order_id"] ?? ""),
        symbol: str(r["symbol"]) ?? "",
        strategy: str(r["strategy"]) ?? "",
        expiration: str(r["expiration"]),
        entryCredit: num(r["entry_credit"]),
        pnl: num(r["pnl"]),
        quantity: num(r["quantity"]),
        openedAt: str(r["opened_at"]),
        closedAt: str(r["closed_at"]),
        profile: str(r["profile"]),
      })),
  );
}

function readReviews(dbPath: string, mode: TradingMode): EntryReviewRow[] {
  return withReadOnlyDb<EntryReviewRow[]>(dbPath, [], (db) => {
    const has = db
      .prepare<[], Record<string, unknown>>("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_reviews'")
      .get();
    if (has === undefined) return [];
    return db
      .prepare<[], Record<string, unknown>>(
        `SELECT scan_date, symbol, timing, winrate, iv_rv_ratio, expected_move,
                best_tier, selected, reason
           FROM entry_reviews ORDER BY id DESC LIMIT 100`,
      )
      .all()
      .map((r: Record<string, unknown>) => ({
        mode,
        scanDate: str(r["scan_date"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        timing: str(r["timing"]),
        winrate: num(r["winrate"]),
        ivRvRatio: num(r["iv_rv_ratio"]),
        expectedMove: num(r["expected_move"]),
        bestTier: str(r["best_tier"]),
        selected: r["selected"] === 1,
        reason: str(r["reason"]),
      }));
  });
}

/** Earnings browses both books at once — every row carries the mode of its source DB. */
export function readEarnings(config: ConsoleConfig): EarningsPayload {
  const liveDb = path.join(config.paths.earningsDir, "earnings_trades.db");
  const paperDb = path.join(config.paths.earningsDir, "paper_trades.db");
  return {
    trades: [...readTrades(liveDb, "live"), ...readTrades(paperDb, "paper")],
    reviews: [...readReviews(liveDb, "live"), ...readReviews(paperDb, "paper")],
  };
}
