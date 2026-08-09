import fs from "node:fs";
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

export interface UpcomingEarningsRow {
  symbol: string;
  earningsDate: string;
  timing: string | null;
  price: number | null;
  expectedMovePct: number | null;
  ivRvRatio: number | null;
  termStructure: number | null;
  winrate: number | null;
  ivRank: number | null;
  tier: string;
  tierReasons: string[];
}

export interface SymbolWatchPayload {
  passStartedAt: number | null;
  passCompletedAt: number | null;
  done: number;
  total: number;
  rows: UpcomingEarningsRow[];
}

/**
 * The earnings module's forward-preview scan (symbol_watch.json), written by
 * its own scheduled task — read with a plain JSON parse, never written. A
 * missing/mid-pass file degrades to an empty result with the pass status.
 */
export function readSymbolWatch(config: ConsoleConfig): SymbolWatchPayload {
  const empty: SymbolWatchPayload = { passStartedAt: null, passCompletedAt: null, done: 0, total: 0, rows: [] };
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(
      fs.readFileSync(path.join(config.paths.earningsDir, "symbol_watch.json"), "utf-8"),
    ) as Record<string, unknown>;
  } catch {
    return empty;
  }
  const symbols = raw["symbols"];
  const rows: UpcomingEarningsRow[] = [];
  if (typeof symbols === "object" && symbols !== null) {
    for (const entry of Object.values(symbols as Record<string, Record<string, unknown>>)) {
      const sym = str(entry["symbol"]);
      const date = str(entry["earnings_date"]);
      if (sym === null || date === null) continue;
      rows.push({
        symbol: sym,
        earningsDate: date,
        timing: str(entry["earnings_timing"]),
        price: num(entry["price"]),
        expectedMovePct: num(entry["expected_move_pct"]),
        ivRvRatio: num(entry["iv_rv_ratio"]),
        termStructure: num(entry["term_structure"]),
        winrate: num(entry["winrate"]),
        ivRank: num(entry["iv_rank"]),
        tier: str(entry["tier"]) ?? "unknown",
        tierReasons: Array.isArray(entry["tier_reasons"]) ? entry["tier_reasons"].map(String) : [],
      });
    }
  }
  rows.sort((a, b) => (a.earningsDate === b.earningsDate ? a.symbol.localeCompare(b.symbol) : a.earningsDate.localeCompare(b.earningsDate)));
  return {
    passStartedAt: num(raw["pass_started_at"]),
    passCompletedAt: num(raw["pass_completed_at"]),
    done: num(raw["done"]) ?? 0,
    total: num(raw["total"]) ?? 0,
    rows,
  };
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
