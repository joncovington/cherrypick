import fs from "node:fs";
import path from "node:path";
import type { EarningsPayload, EarningsTradeRow, EntryReviewRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra, withReadOnlyDb, num, str } from "./db.js";
import { pageArray, FIRST_PAGE, type PageRequest } from "./paging.js";
import { readMeasurementBreaks, readSchemaDrift } from "./integrity.js";
import { isoStamp, sessionDate } from "../services/report.js";

/**
 * The era bound for earnings' multi-session surfaces.
 *
 * Earnings has no era column of its own — its boundary is the suite's `data_epoch` (the
 * 2026-08-21 advisor-era cutover, journaled in its measurement_breaks). "ALL" widens to full
 * history, the same convention MEIC's and flies' era scopes use; anything else means the current
 * era. Bounded on the ENTRY stamp: the rules in force at entry are what formed the trade, so a
 * position straddling the boundary belongs to the era that opened it.
 */
function eraSince(config: ConsoleConfig, era: string | null): string | null {
  if (era === "ALL") return null;
  return suiteEra(config.paths.orchestratorConfig).from;
}

/** Whether an epoch/ISO stamp falls on/after `since` (null since = no bound; null stamp = keep,
 *  because "not recorded" must not silently vanish from a browse). */
function onOrAfter(stamp: unknown, since: string | null): boolean {
  if (since === null) return true;
  const session = sessionDate(stamp);
  return session === null || session >= since;
}

function readTrades(dbPath: string, mode: TradingMode, since: string | null): EarningsTradeRow[] {
  return withReadOnlyDb<EarningsTradeRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT order_id, symbol, strategy, expiration, entry_credit, pnl, quantity,
                opened_at, closed_at, profile
           FROM trades ORDER BY opened_at DESC`,
      )
      .all()
      // In JS rather than SQL: opened_at is an epoch float and sessionDate already owns the
      // stamp-to-session conversion — a second implementation in SQL would be free to disagree.
      .filter((r: Record<string, unknown>) => onOrAfter(r["opened_at"], since))
      .map((r: Record<string, unknown>) => ({
        mode,
        orderId: String(r["order_id"] ?? ""),
        symbol: str(r["symbol"]) ?? "",
        strategy: str(r["strategy"]) ?? "",
        expiration: str(r["expiration"]),
        entryCredit: num(r["entry_credit"]),
        pnl: num(r["pnl"]),
        quantity: num(r["quantity"]),
        // Epoch floats in this store, not strings — see isoStamp.
        openedAt: isoStamp(r["opened_at"]),
        closedAt: isoStamp(r["closed_at"]),
        profile: str(r["profile"]),
      })),
  );
}

function readReviews(dbPath: string, mode: TradingMode, since: string | null): EntryReviewRow[] {
  return withReadOnlyDb<EntryReviewRow[]>(dbPath, [], (db) => {
    const has = db
      .prepare<[], Record<string, unknown>>("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_reviews'")
      .get();
    if (has === undefined) return [];
    return db
      .prepare<[], Record<string, unknown>>(
        `SELECT scan_date, symbol, timing, winrate, iv_rv_ratio, expected_move,
                selected, reason
           FROM entry_reviews ORDER BY id DESC`,
      )
      .all()
      .filter((r: Record<string, unknown>) => since === null || (str(r["scan_date"]) ?? "") >= since)
      .map((r: Record<string, unknown>) => ({
        mode,
        scanDate: str(r["scan_date"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        timing: str(r["timing"]),
        winrate: num(r["winrate"]),
        ivRvRatio: num(r["iv_rv_ratio"]),
        expectedMove: num(r["expected_move"]),
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

/** The strategy dashboard's significance target — the sample a strategy needs to mean something. */
const SIGNIFICANT_TARGET = 30;
const DIRECTIONAL_TARGET = 10;

export interface EarningsDetail {
  mode: TradingMode;
  /** Closed-trade equity by settlement date, for cumulative and rolling windows. */
  equity: Array<{ date: string; net: number; cumulative: number }>;
  perStrategy: Array<{
    strategy: string;
    trades: number;
    winRatePct: number | null;
    profitFactor: number | null;
    expectancy: number | null;
    net: number;
    sharpe: number | null;
    maxDrawdown: number;
    maxDrawdownPct: number | null;
    avgIvCrushPts: number | null;
    ivSample: number;
    sampleProgress: number;
    significant: boolean;
    directional: boolean;
    curve: Array<{ i: number; equity: number; drawdown: number }>;
  }>;
  /** IV/RV × dispersion buckets — where the sample actually lives. */
  regimeHeat: {
    ivRvBuckets: string[];
    dispersionBuckets: string[];
    cells: Array<{ strategy: string; ivRv: string; dispersion: string; trades: number }>;
  };
  capitalAtRisk: number;
}

function bucketIvRv(v: number | null): string {
  if (v === null) return "unknown";
  if (v < 1.0) return "<1.0";
  if (v < 1.2) return "1.0–1.2";
  if (v < 1.5) return "1.2–1.5";
  return "≥1.5";
}

function bucketDispersion(v: number | null): string {
  if (v === null) return "unknown";
  if (v < 0.02) return "<2%";
  if (v < 0.04) return "2–4%";
  if (v < 0.07) return "4–7%";
  return "≥7%";
}

export function readEarningsDetail(
  config: ConsoleConfig,
  mode: TradingMode,
  era: string | null = null,
): EarningsDetail {
  const since = eraSince(config, era);
  const file = mode === "live" ? "earnings_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.earningsDir, file);
  const empty: EarningsDetail = {
    mode,
    equity: [],
    perStrategy: [],
    regimeHeat: { ivRvBuckets: [], dispersionBuckets: [], cells: [] },
    capitalAtRisk: 0,
  };
  return withReadOnlyDb<EarningsDetail>(dbPath, empty, (db) => {
    const closed = db
      .prepare<[], Record<string, unknown>>(
        `SELECT strategy, symbol, opened_at, closed_at, entry_iv, exit_iv, entry_context, capital_at_risk,
                pnl - COALESCE(entry_cost, 0) - COALESCE(exit_cost, 0) AS net
           FROM trades WHERE closed_at IS NOT NULL AND pnl IS NOT NULL ORDER BY closed_at`,
      )
      .all()
      .filter((r) => onOrAfter(r["opened_at"], since));

    // --- daily equity ---
    const byDate = new Map<string, number>();
    for (const r of closed) {
      const d = sessionDate(r["closed_at"]);
      if (d === null) continue;
      byDate.set(d, (byDate.get(d) ?? 0) + Number(r["net"]));
    }
    let cum = 0;
    const equity = [...byDate.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, net]) => {
        cum += net;
        return { date, net, cumulative: cum };
      });

    // --- per strategy ---
    const groups = new Map<string, Array<Record<string, unknown>>>();
    for (const r of closed) {
      const k = String(r["strategy"] ?? "?");
      let list = groups.get(k);
      if (list === undefined) {
        list = [];
        groups.set(k, list);
      }
      list.push(r);
    }
    const perStrategy = [...groups.entries()]
      .map(([strategy, rs]) => {
        const nets = rs.map((r) => Number(r["net"]));
        const wins = nets.filter((n) => n > 0);
        const losses = nets.filter((n) => n < 0);
        const gw = wins.reduce((s, n) => s + n, 0);
        const gl = Math.abs(losses.reduce((s, n) => s + n, 0));
        const net = nets.reduce((s, n) => s + n, 0);
        // Trade-level Sharpe: mean/sd of per-trade nets (not annualized — an
        // earnings trade is an event, not a period).
        const mean = nets.length > 0 ? net / nets.length : 0;
        const sd =
          nets.length >= 2
            ? Math.sqrt(nets.reduce((s, n) => s + (n - mean) ** 2, 0) / (nets.length - 1))
            : null;
        let running = 0;
        let peak = 0;
        let maxdd = 0;
        const curve = nets.map((n, i) => {
          running += n;
          peak = Math.max(peak, running);
          maxdd = Math.max(maxdd, peak - running);
          return { i, equity: running, drawdown: peak - running };
        });
        const ivPairs = rs
          .map((r) => {
            const a = num(r["entry_iv"]);
            const b = num(r["exit_iv"]);
            return a !== null && b !== null ? (a - b) * 100 : null;
          })
          .filter((v): v is number => v !== null);
        const capital = rs.reduce((s, r) => s + (num(r["capital_at_risk"]) ?? 0), 0);
        return {
          strategy,
          trades: rs.length,
          winRatePct: wins.length + losses.length > 0 ? (wins.length / (wins.length + losses.length)) * 100 : null,
          profitFactor: gl > 0 ? gw / gl : null,
          expectancy: nets.length > 0 ? net / nets.length : null,
          net,
          sharpe: sd !== null && sd !== 0 ? Math.round((mean / sd) * 1000) / 1000 : null,
          maxDrawdown: maxdd,
          maxDrawdownPct: capital > 0 ? (maxdd / capital) * 100 : null,
          avgIvCrushPts: ivPairs.length > 0 ? ivPairs.reduce((s, v) => s + v, 0) / ivPairs.length : null,
          ivSample: ivPairs.length,
          sampleProgress: Math.min(1, rs.length / SIGNIFICANT_TARGET),
          significant: rs.length >= SIGNIFICANT_TARGET,
          directional: rs.length >= DIRECTIONAL_TARGET,
          curve,
        };
      })
      .sort((a, b) => b.net - a.net);

    // --- regime heat: where the sample lives ---
    const cellMap = new Map<string, number>();
    const ivRvSet = new Set<string>();
    const dispSet = new Set<string>();
    for (const r of closed) {
      let ctx: Record<string, unknown> = {};
      try {
        ctx = JSON.parse(String(r["entry_context"] ?? "{}")) as Record<string, unknown>;
      } catch {
        /* untagged */
      }
      const iv = bucketIvRv(num(ctx["iv_rv_ratio"]));
      const dp = bucketDispersion(num(ctx["dispersion"]));
      ivRvSet.add(iv);
      dispSet.add(dp);
      const key = `${String(r["strategy"])}|${iv}|${dp}`;
      cellMap.set(key, (cellMap.get(key) ?? 0) + 1);
    }
    const cells = [...cellMap.entries()].map(([k, trades]) => {
      const [strategy, ivRv, dispersion] = k.split("|");
      return { strategy: strategy!, ivRv: ivRv!, dispersion: dispersion!, trades };
    });

    // The rejection histogram used to be built here, straight off scan_log. It disagreed with
    // screen_report about which gate to move -- it pooled four incompatible reason vocabularies and
    // had no sole-blocker column, so it ranked gates that fire constantly but never alone. The
    // classified version now comes from the module itself, via /api/earnings/screen.
    const capitalAtRisk =
      num(
        (db
          .prepare<[], Record<string, unknown>>(
            "SELECT COALESCE(SUM(capital_at_risk), 0) AS c FROM trades WHERE closed_at IS NULL",
          )
          .get() ?? {})["c"],
      ) ?? 0;

    return {
      mode,
      equity,
      perStrategy,
      regimeHeat: {
        ivRvBuckets: [...ivRvSet].sort(),
        dispersionBuckets: [...dispSet].sort(),
        cells,
      },
      capitalAtRisk,
    };
  });
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

export function readEarningsAnalytics(
  config: ConsoleConfig,
  mode: TradingMode,
  era: string | null = null,
): EarningsAnalytics {
  const since = eraSince(config, era);
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
        `SELECT opened_at, closed_at, strategy,
                pnl - COALESCE(entry_cost, 0) - COALESCE(exit_cost, 0) AS net
           FROM trades WHERE closed_at IS NOT NULL AND pnl IS NOT NULL`,
      )
      .all()
      // Era-bounded on the ENTRY stamp; open positions below stay unbounded because "open" is
      // current by definition.
      .filter((r) => onOrAfter(r["opened_at"], since));
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

export interface EarningsPageRequest {
  trades: PageRequest;
  reviews: PageRequest;
}

export const EARNINGS_FIRST_PAGE: EarningsPageRequest = { trades: FIRST_PAGE, reviews: FIRST_PAGE };

/**
 * Earnings browses both books at once — every row carries the mode of its
 * source DB. Because the list spans two stores, neither can page it: a LIMIT in
 * either one would cut rows the merged ordering has not placed yet. So both are
 * read whole, merged, ordered, and paged in memory. The cost is proportional to
 * the module's total trade count, which is small and grows by a handful a week;
 * the alternative — a per-store LIMIT — silently drops rows, which is what this
 * is here to stop.
 */
export function readEarnings(
  config: ConsoleConfig,
  page: EarningsPageRequest = EARNINGS_FIRST_PAGE,
  era: string | null = null,
): EarningsPayload {
  const since = eraSince(config, era);
  const liveDb = path.join(config.paths.earningsDir, "earnings_trades.db");
  const paperDb = path.join(config.paths.earningsDir, "paper_trades.db");
  const trades = [...readTrades(liveDb, "live", since), ...readTrades(paperDb, "paper", since)].sort((a, b) =>
    (b.openedAt ?? "").localeCompare(a.openedAt ?? ""),
  );
  const reviews = [...readReviews(liveDb, "live", since), ...readReviews(paperDb, "paper", since)].sort((a, b) =>
    b.scanDate.localeCompare(a.scanDate),
  );
  // The methodology journal lives in the PAPER ledger -- the live one has no such table -- and the
  // breaks describe the module's rules, which govern both books. Five are on file, including the
  // 2026-08-12 lifecycle cutover that changed when a position is entered and closed.
  const integrity = withReadOnlyDb<EarningsPayload["integrity"]>(
    paperDb,
    { measurementBreaks: [], schemaDrift: [], books: { live: 0, paper: 0 }, breakDetail: [] },
    (db) => ({
      measurementBreaks: readMeasurementBreaks(db),
      schemaDrift: readSchemaDrift(db, EARNINGS_KNOWN_COLUMNS),
      books: {
        live: trades.filter((t) => t.mode === "live").length,
        paper: trades.filter((t) => t.mode === "paper").length,
      },
      breakDetail: db
        .prepare<[], Record<string, unknown>>(
          "SELECT key, old_value, new_value FROM measurement_breaks ORDER BY break_date DESC, id DESC",
        )
        .all()
        .map((r) => ({
          key: String(r["key"] ?? ""),
          from: typeof r["old_value"] === "string" ? r["old_value"] : null,
          to: typeof r["new_value"] === "string" ? r["new_value"] : null,
        })),
    }),
  );

  return { trades: pageArray(trades, page.trades), reviews: pageArray(reviews, page.reviews), integrity };
}

const EARNINGS_KNOWN_COLUMNS: Record<string, string[]> = {
  measurement_breaks: ["id", "break_date", "key", "old_value", "new_value", "note", "recorded_at"],
};
