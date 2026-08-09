import fs from "node:fs";
import path from "node:path";
import type { EarningsPayload, EarningsTradeRow, EntryReviewRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";
import { sessionDate } from "../services/report.js";

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

export interface EarningsAnalytics {
  mode: TradingMode;
  /** Strategy-dashboard KPIs: net = pnl − entry_cost − exit_cost on closed trades. */
  kpis: { totalNet: number; closedTrades: number; expectancy: number | null; strategiesActive: number };
  openPositions: Array<{
    strategy: string;
    symbol: string;
    quantity: number | null;
    credit: number | null;
    netOfCost: number | null;
    maxLoss: number | null;
    entryCost: number | null;
    expiration: string | null;
  }>;
  weekly: Array<{ week: string; net: number }>;
  /** Cross-strategy comparison: win rate, profit factor, expectancy on net. */
  strategies: Array<{
    strategy: string;
    trades: number;
    winRatePct: number | null;
    profitFactor: number | null;
    expectancy: number | null;
    net: number;
  }>;
}

export function readEarningsAnalytics(config: ConsoleConfig, mode: TradingMode): EarningsAnalytics {
  const file = mode === "live" ? "earnings_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.earningsDir, file);
  const empty: EarningsAnalytics = {
    mode,
    kpis: { totalNet: 0, closedTrades: 0, expectancy: null, strategiesActive: 0 },
    openPositions: [],
    weekly: [],
    strategies: [],
  };
  return withReadOnlyDb<EarningsAnalytics>(dbPath, empty, (db) => {
    const closed = db
      .prepare<[], Record<string, unknown>>(
        `SELECT closed_at, strategy,
                pnl - COALESCE(entry_cost, 0) - COALESCE(exit_cost, 0) AS net
           FROM trades WHERE closed_at IS NOT NULL AND pnl IS NOT NULL`,
      )
      .all();
    const totalNet = closed.reduce((s, r) => s + Number(r["net"]), 0);
    const strategies = new Set(closed.map((r) => String(r["strategy"])));

    const weeklyMap = new Map<string, number>();
    for (const r of closed) {
      const session = sessionDate(r["closed_at"]);
      if (session === null) continue;
      const d = new Date(session + "T00:00:00Z");
      if (Number.isNaN(d.getTime())) continue;
      // ISO week key YYYY-Www.
      const day = (d.getUTCDay() + 6) % 7;
      const thursday = new Date(d);
      thursday.setUTCDate(d.getUTCDate() - day + 3);
      const jan1 = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 1));
      const week = Math.ceil(((thursday.getTime() - jan1.getTime()) / 86_400_000 + 1) / 7);
      const key = `${thursday.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
      weeklyMap.set(key, (weeklyMap.get(key) ?? 0) + Number(r["net"]));
    }

    const openPositions = db
      .prepare<[], Record<string, unknown>>(
        `SELECT strategy, symbol, quantity, entry_credit, capital_at_risk, entry_cost, expiration
           FROM trades WHERE closed_at IS NULL ORDER BY expiration`,
      )
      .all()
      .map((r) => {
        const credit = num(r["entry_credit"]);
        const entryCost = num(r["entry_cost"]);
        return {
          strategy: str(r["strategy"]) ?? "?",
          symbol: str(r["symbol"]) ?? "?",
          quantity: num(r["quantity"]),
          credit: credit !== null ? credit * 100 : null,
          netOfCost: credit !== null ? credit * 100 - (entryCost ?? 0) : null,
          maxLoss: num(r["capital_at_risk"]),
          entryCost,
          expiration: str(r["expiration"]),
        };
      });

    const byStrategy = new Map<string, number[]>();
    for (const r of closed) {
      const key = String(r["strategy"] ?? "?");
      let list = byStrategy.get(key);
      if (list === undefined) {
        list = [];
        byStrategy.set(key, list);
      }
      list.push(Number(r["net"]));
    }
    const strategyRows = [...byStrategy.entries()]
      .map(([strategy, nets]) => {
        const wins = nets.filter((n) => n > 0);
        const losses = nets.filter((n) => n < 0);
        const won = wins.reduce((s, n) => s + n, 0);
        const lost = Math.abs(losses.reduce((s, n) => s + n, 0));
        const net = nets.reduce((s, n) => s + n, 0);
        return {
          strategy,
          trades: nets.length,
          winRatePct: wins.length + losses.length > 0 ? (wins.length / (wins.length + losses.length)) * 100 : null,
          profitFactor: lost > 0 ? won / lost : null,
          expectancy: nets.length > 0 ? net / nets.length : null,
          net,
        };
      })
      .sort((a, b) => b.net - a.net);

    return {
      mode,
      kpis: {
        totalNet,
        closedTrades: closed.length,
        expectancy: closed.length > 0 ? totalNet / closed.length : null,
        strategiesActive: strategies.size,
      },
      openPositions,
      weekly: [...weeklyMap.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([week, net]) => ({ week, net })),
      strategies: strategyRows,
    };
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
