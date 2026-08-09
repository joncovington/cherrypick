import path from "node:path";
import type { MeicPayload, MeicTradeRow, MeicSummaryRow, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import type { DatabaseHandle } from "./db.js";
import { withReadOnlyDb, hasColumn, num, str } from "./db.js";

const TRADE_LIMIT = 300;

/**
 * The era the module counts as evidence. Its own analytics narrow to this by
 * default, so anything tagged to an earlier era is bring-up and shakedown data
 * — mixing it in silently distorts every breakdown. Duplicated as a literal
 * rather than imported so the packages stay decoupled; kept in step with
 * `CURRENT_ERA` in `packages/meic/.../analytics.py`.
 */
export const CURRENT_ERA = "sample";

export interface MeicScopeFilter {
  symbol: string | null;
  profile: string | null;
  /** null = the current era; "ALL" = every era, deliberately. */
  era: string | null;
}

export const NO_SCOPE: MeicScopeFilter = { symbol: null, profile: null, era: null };

/** Page-wide scope (era × symbol × profile), the shape every MEIC read is narrowed by. */
function scopeSql(db: DatabaseHandle, scope: MeicScopeFilter): { and: string; params: string[] } {
  const clauses: string[] = [];
  const params: string[] = [];
  const era = scope.era ?? CURRENT_ERA;
  if (era !== "ALL" && hasColumn(db, "ic_trades", "era")) {
    clauses.push("era = ?");
    params.push(era);
  }
  if (scope.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(scope.symbol);
  }
  if (scope.profile !== null) {
    clauses.push("risk_profile = ?");
    params.push(scope.profile);
  }
  return { and: clauses.length > 0 ? ` AND ${clauses.join(" AND ")}` : "", params };
}

export function readMeic(config: ConsoleConfig, mode: TradingMode, scope: MeicScopeFilter = NO_SCOPE): MeicPayload {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);

  const trades = withReadOnlyDb<MeicTradeRow[]>(dbPath, [], (db) => {
    const sc = scopeSql(db, scope);
    return db
      .prepare<string[], Record<string, unknown>>(
        `SELECT id, trade_date, entry_time, symbol, put_strike, call_strike, wing_width,
                net_credit, quantity, status, pnl, fees, exit_reason, iv_rank_at_entry
           FROM ic_trades WHERE 1=1${sc.and} ORDER BY id DESC LIMIT ${TRADE_LIMIT}`,
      )
      .all(...sc.params)
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
      }));
  });

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

const BANKROLL_BASE = 100_000;

export interface MeicScope {
  symbols: string[];
  profiles: string[];
  /** Every era present, with its row count, so the page can say what a filter costs. */
  eras: Array<{ era: string; trades: number }>;
  currentEra: string;
}

export function readMeicScope(config: ConsoleConfig, mode: TradingMode): MeicScope {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicScope = { symbols: [], profiles: [], eras: [], currentEra: CURRENT_ERA };
  return withReadOnlyDb<MeicScope>(dbPath, empty, (db) => ({
    eras: hasColumn(db, "ic_trades", "era")
      ? db
          .prepare<[], { era: string; trades: number }>(
            `SELECT era, COUNT(*) AS trades FROM ic_trades
              WHERE era IS NOT NULL GROUP BY era ORDER BY era`,
          )
          .all()
      : [],
    currentEra: CURRENT_ERA,
    symbols: db
      .prepare<[], { s: string }>("SELECT DISTINCT symbol AS s FROM ic_trades WHERE symbol IS NOT NULL ORDER BY symbol")
      .all()
      .map((r) => r.s),
    profiles: db
      .prepare<[], { p: string }>(
        "SELECT DISTINCT risk_profile AS p FROM ic_trades WHERE risk_profile IS NOT NULL ORDER BY risk_profile",
      )
      .all()
      .map((r) => r.p),
  }));
}

export interface MeicLoopStatus {
  /** LIVE when the newest loop row is under 10 minutes old (the reference's rule), else IDLE. */
  state: "live" | "idle" | "no-data";
  lastLoopAt: string | null;
  ageSeconds: number | null;
  action: string | null;
  ivRank: number | null;
  underlyingPrice: number | null;
  sessionQuality: string | null;
}

export function readMeicLoopStatus(config: ConsoleConfig, mode: TradingMode, scope: MeicScopeFilter = NO_SCOPE): MeicLoopStatus {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicLoopStatus = {
    state: "no-data", lastLoopAt: null, ageSeconds: null, action: null,
    ivRank: null, underlyingPrice: null, sessionQuality: null,
  };
  return withReadOnlyDb<MeicLoopStatus>(dbPath, empty, (db) => {
    const symClause = scope.symbol !== null ? " WHERE symbol = ?" : "";
    const params: string[] = scope.symbol !== null ? [scope.symbol] : [];
    const r = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT loop_time, action, iv_rank, underlying_price, session_quality
           FROM loop_log${symClause} ORDER BY id DESC LIMIT 1`,
      )
      .get(...params);
    if (r === undefined) return empty;
    const lastLoopAt = str(r["loop_time"]);
    let ageSeconds: number | null = null;
    if (lastLoopAt !== null) {
      const t = Date.parse(lastLoopAt.includes("T") ? lastLoopAt : lastLoopAt.replace(" ", "T"));
      if (!Number.isNaN(t)) ageSeconds = Math.max(0, (Date.now() - t) / 1000);
    }
    return {
      state: ageSeconds !== null && ageSeconds < 600 ? "live" : "idle",
      lastLoopAt,
      ageSeconds,
      action: str(r["action"]),
      ivRank: num(r["iv_rank"]),
      underlyingPrice: num(r["underlying_price"]),
      sessionQuality: str(r["session_quality"]),
    };
  });
}

export interface MeicPerformance {
  mode: TradingMode;
  /** Every profile side by side — the variance-test payoff; symbol-scoped, profile-unscoped. */
  profiles: Array<{
    profile: string;
    trades: number;
    sessions: number;
    grossPnl: number;
    fees: number;
    netPnl: number;
    winRatePct: number | null;
    expectancy: number | null;
    profitFactor: number | null;
    maxDrawdown: number;
  }>;
  /** Daily equity path on a $100k bankroll, with the underwater curve. */
  equity: Array<{ date: string; netPnl: number; equity: number; drawdown: number }>;
  risk: {
    sharpe: number | null;
    sortino: number | null;
    calmar: number | null;
    recoveryFactor: number | null;
    sampleSize: number;
    sharpeOverfitFlag: boolean;
  };
  periods: Array<{
    period: string;
    trades: number;
    netPnl: number;
    cumulative: number;
    winRatePct: number | null;
    profitFactor: number | null;
    avgWin: number | null;
    avgLoss: number | null;
    expectancy: number | null;
  }>;
  /** One cumulative net line per (symbol × arm) study stream. */
  studyArms: Array<{ arm: string; points: Array<{ date: string; cumulative: number }> }>;
  bySession: MeicBreakdownRow[];
  byIvRank: MeicBreakdownRow[];
  regimeCoverage: Array<{ dimension: string; tagged: number; untagged: number; coveragePct: number; degenerate: boolean }>;
}

function stdev(values: number[]): number | null {
  if (values.length < 2) return null;
  const m = values.reduce((s, v) => s + v, 0) / values.length;
  const varr = values.reduce((s, v) => s + (v - m) ** 2, 0) / (values.length - 1);
  return Math.sqrt(varr);
}

function periodKey(granularity: string, tradeDate: string): string {
  if (granularity === "monthly") return tradeDate.slice(0, 7);
  if (granularity === "weekly") {
    const d = new Date(tradeDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  return tradeDate;
}

const REGIME_DIMENSIONS: Array<[string, string]> = [
  ["vol_implied", "entry_vol_implied_bucket"],
  ["vol_event", "entry_vol_event_bucket"],
  ["vol_realized", "entry_vol_realized_bucket"],
  ["vol_intraday", "entry_vol_intraday_bucket"],
  ["gex", "entry_gex_bucket"],
  ["skew", "entry_skew_bucket"],
  ["center_offset", "entry_center_offset_bucket"],
  ["trend", "entry_trend_bucket"],
];

export function readMeicPerformance(
  config: ConsoleConfig,
  mode: TradingMode,
  granularity: string,
  symbol: string | null,
  profile: string | null,
  era: string | null = null,
): MeicPerformance {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicPerformance = {
    mode,
    profiles: [],
    equity: [],
    risk: { sharpe: null, sortino: null, calmar: null, recoveryFactor: null, sampleSize: 0, sharpeOverfitFlag: false },
    periods: [],
    studyArms: [],
    bySession: [],
    byIvRank: [],
    regimeCoverage: [],
  };
  return withReadOnlyDb<MeicPerformance>(dbPath, empty, (db) => {
    const hasProfile = hasColumn(db, "ic_trades", "risk_profile");
    // Era narrows every figure on the page, including the cross-profile and
    // cross-arm comparisons — comparing a profile's book-era rows against
    // another's sample-era rows would not be a comparison at all.
    const activeEra = era ?? CURRENT_ERA;
    const eraOn = activeEra !== "ALL" && hasColumn(db, "ic_trades", "era");

    const clauses = [RESOLVED];
    const params: string[] = [];
    if (eraOn) {
      clauses.push("era = ?");
      params.push(activeEra);
    }
    if (symbol !== null) {
      clauses.push("symbol = ?");
      params.push(symbol);
    }
    if (profile !== null && hasProfile) {
      clauses.push("risk_profile = ?");
      params.push(profile);
    }
    const where = clauses.join(" AND ");

    // --- profile comparison: symbol-scoped, profile-unscoped (compare them all) ---
    const profileClauses = [RESOLVED, "risk_profile IS NOT NULL"];
    const profileParams: string[] = [];
    if (eraOn) {
      profileClauses.push("era = ?");
      profileParams.push(activeEra);
    }
    if (symbol !== null) {
      profileClauses.push("symbol = ?");
      profileParams.push(symbol);
    }
    const profiles = hasProfile
      ? (() => {
          const rows = db
            .prepare<string[], Record<string, unknown>>(
              `SELECT risk_profile, trade_date, pnl, fees FROM ic_trades
                WHERE ${profileClauses.join(" AND ")} ORDER BY risk_profile, trade_date, entry_time`,
            )
            .all(...profileParams);
          const groups = new Map<string, Array<Record<string, unknown>>>();
          for (const r of rows) {
            const k = String(r["risk_profile"]);
            let list = groups.get(k);
            if (list === undefined) {
              list = [];
              groups.set(k, list);
            }
            list.push(r);
          }
          return [...groups.entries()]
            .map(([prof, rs]) => {
              const nets = rs.map((r) => Number(r["pnl"] ?? 0) - Number(r["fees"] ?? 0));
              const gross = rs.reduce((s, r) => s + Number(r["pnl"] ?? 0), 0);
              const fees = rs.reduce((s, r) => s + Number(r["fees"] ?? 0), 0);
              const resolvedNets = rs.filter((r) => r["pnl"] !== null).map((r) => Number(r["pnl"]) - Number(r["fees"] ?? 0));
              const wins = resolvedNets.filter((n) => n > 0).length;
              const gw = nets.filter((n) => n > 0).reduce((s, n) => s + n, 0);
              const gl = Math.abs(nets.filter((n) => n <= 0).reduce((s, n) => s + n, 0));
              // Real equity path (rows are date/time ordered), not a sorted approximation.
              let running = 0;
              let peak = 0;
              let maxdd = 0;
              for (const n of nets) {
                running += n;
                peak = Math.max(peak, running);
                maxdd = Math.max(maxdd, peak - running);
              }
              return {
                profile: prof,
                trades: rs.length,
                sessions: new Set(rs.map((r) => String(r["trade_date"]))).size,
                grossPnl: gross,
                fees,
                netPnl: gross - fees,
                winRatePct: resolvedNets.length > 0 ? (wins / resolvedNets.length) * 100 : null,
                expectancy: rs.length > 0 ? (gross - fees) / rs.length : null,
                profitFactor: gl > 0 ? gw / gl : null,
                maxDrawdown: maxdd,
              };
            })
            .sort((a, b) => b.netPnl - a.netPnl);
        })()
      : [];

    // --- daily equity + drawdown (net = SUM(pnl), the stats-grid convention) ---
    const dailyRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, COALESCE(SUM(pnl), 0) AS net FROM ic_trades WHERE ${where}
          GROUP BY trade_date ORDER BY trade_date`,
      )
      .all(...params);
    let cum = 0;
    let peak = 0;
    const equity = dailyRows.map((r) => {
      const net = Number(r["net"]);
      cum += net;
      peak = Math.max(peak, cum);
      return { date: String(r["trade_date"]), netPnl: net, equity: BANKROLL_BASE + cum, drawdown: peak - cum };
    });

    // --- risk-adjusted metrics from the DAILY series regardless of display granularity ---
    const returns = equity.map((b) => b.netPnl / BANKROLL_BASE);
    const n = returns.length;
    const meanR = n > 0 ? returns.reduce((s, v) => s + v, 0) / n : 0;
    const sd = stdev(returns);
    const downside = returns.filter((r) => r < 0);
    const ddSd = downside.length >= 2 ? stdev(downside) : null;
    const maxDd = Math.max(...equity.map((b) => b.drawdown), 0);
    const totalReturn = returns.reduce((s, v) => s + v, 0);
    const annualized = n > 0 ? totalReturn * (252 / n) : 0;
    const maxDdPct = maxDd / BANKROLL_BASE;
    const netTotal = equity.reduce((s, b) => s + b.netPnl, 0);
    const sharpe = sd !== null && sd !== 0 ? (meanR / sd) * Math.sqrt(252) : null;
    const risk = {
      sharpe: sharpe !== null ? Math.round(sharpe * 1000) / 1000 : null,
      sortino: ddSd !== null && ddSd !== 0 ? Math.round((meanR / ddSd) * Math.sqrt(252) * 1000) / 1000 : null,
      calmar: maxDdPct > 0 ? Math.round((annualized / maxDdPct) * 1000) / 1000 : null,
      recoveryFactor: maxDd > 0 ? Math.round((netTotal / maxDd) * 1000) / 1000 : null,
      sampleSize: n,
      sharpeOverfitFlag: sharpe !== null && sharpe > 3,
    };

    // --- per-period series ---
    const tradeRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, pnl, fees FROM ic_trades WHERE ${where} ORDER BY trade_date`,
      )
      .all(...params);
    const buckets = new Map<string, number[]>();
    for (const r of tradeRows) {
      const key = periodKey(granularity, String(r["trade_date"]));
      let list = buckets.get(key);
      if (list === undefined) {
        list = [];
        buckets.set(key, list);
      }
      if (r["pnl"] !== null) list.push(Number(r["pnl"]) - Number(r["fees"] ?? 0));
    }
    let runningCum = 0;
    const periods = [...buckets.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([period, nets]) => {
        const net = nets.reduce((s, v) => s + v, 0);
        runningCum += net;
        const wins = nets.filter((v) => v > 0);
        const losses = nets.filter((v) => v <= 0);
        const gw = wins.reduce((s, v) => s + v, 0);
        const gl = Math.abs(losses.reduce((s, v) => s + v, 0));
        return {
          period,
          trades: nets.length,
          netPnl: net,
          cumulative: runningCum,
          winRatePct: nets.length > 0 ? (wins.length / nets.length) * 100 : null,
          profitFactor: gl > 0 ? gw / gl : null,
          avgWin: wins.length > 0 ? gw / wins.length : null,
          avgLoss: losses.length > 0 ? -gl / losses.length : null,
          expectancy: nets.length > 0 ? net / nets.length : null,
        };
      });

    // --- study arms: one cumulative line per profile (ignores the page filters, but not the era) ---
    const studyArms = hasProfile
      ? (() => {
          const rows = db
            .prepare<string[], Record<string, unknown>>(
              `SELECT risk_profile, trade_date, COALESCE(SUM(pnl), 0) AS net FROM ic_trades
                WHERE ${RESOLVED} AND risk_profile IS NOT NULL${eraOn ? " AND era = ?" : ""}
                GROUP BY risk_profile, trade_date ORDER BY risk_profile, trade_date`,
            )
            .all(...(eraOn ? [activeEra] : []));
          const byArm = new Map<string, Array<{ date: string; cumulative: number }>>();
          const running = new Map<string, number>();
          for (const r of rows) {
            const arm = String(r["risk_profile"]);
            const next = (running.get(arm) ?? 0) + Number(r["net"]);
            running.set(arm, next);
            let list = byArm.get(arm);
            if (list === undefined) {
              list = [];
              byArm.set(arm, list);
            }
            list.push({ date: String(r["trade_date"]), cumulative: next });
          }
          return [...byArm.entries()].map(([arm, points]) => ({ arm, points }));
        })()
      : [];

    // --- breakdowns the reference groups on entry conditions ---
    const breakdown = (bucketExpr: string): MeicBreakdownRow[] =>
      db
        .prepare<string[], Record<string, unknown>>(
          `SELECT ${bucketExpr} AS bucket, COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
                  SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                  AVG(pnl - COALESCE(fees, 0)) AS avg_net
             FROM ic_trades WHERE ${where} AND pnl IS NOT NULL
            GROUP BY bucket ORDER BY bucket`,
        )
        .all(...params)
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

    const regimeCoverage = REGIME_DIMENSIONS.flatMap(([dimension, col]) => {
      try {
        const r =
          db
            .prepare<string[], Record<string, unknown>>(
              `SELECT SUM(CASE WHEN ${col} IS NOT NULL AND ${col} != '' THEN 1 ELSE 0 END) AS tagged,
                      SUM(CASE WHEN ${col} IS NULL OR ${col} = '' THEN 1 ELSE 0 END) AS untagged,
                      COUNT(DISTINCT ${col}) AS buckets
                 FROM ic_trades WHERE ${where}`,
            )
            .get(...params) ?? {};
        const tagged = Number(r["tagged"] ?? 0);
        const untagged = Number(r["untagged"] ?? 0);
        const total = tagged + untagged;
        return [
          {
            dimension,
            tagged,
            untagged,
            coveragePct: total > 0 ? (tagged / total) * 100 : 0,
            // One bucket for every tagged row says nothing — the dimension is degenerate here.
            degenerate: tagged > 0 && Number(r["buckets"] ?? 0) <= 1,
          },
        ];
      } catch {
        return [];
      }
    });

    return {
      mode,
      profiles,
      equity,
      risk,
      periods,
      studyArms,
      bySession: breakdown("COALESCE(session_quality, 'untagged')"),
      byIvRank: breakdown(
        `CASE WHEN iv_rank_at_entry IS NULL THEN 'unknown'
              WHEN iv_rank_at_entry < 0.25 THEN '<25%'
              WHEN iv_rank_at_entry < 0.50 THEN '25-50%'
              WHEN iv_rank_at_entry < 0.75 THEN '50-75%'
              ELSE '>75%' END`,
      ),
      regimeCoverage,
    };
  });
}

export interface MeicBreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

/**
 * The two-digit ET entry hour, or NULL when the row has no usable one.
 * `entry_time` has drifted across the module's life: a full timestamp with an
 * offset (`2026-07-10 09:34:49.29-04:00`), one with a zone suffix
 * (`2026-06-29 10:01:30 ET`), an ISO `T` separator, and — in the earliest live
 * rows — a bare `HH:MM`. Anything else buckets as unknown rather than as hour
 * zero, which is how a bare time used to read.
 */
const ENTRY_HOUR = `
  CASE
    WHEN entry_time IS NULL THEN NULL
    WHEN substr(entry_time, 12, 2) GLOB '[0-9][0-9]' THEN substr(entry_time, 12, 2)
    WHEN substr(entry_time, 1, 2) GLOB '[0-9][0-9]' AND substr(entry_time, 3, 1) = ':'
      THEN substr(entry_time, 1, 2)
    ELSE NULL
  END`;

export interface MeicDeepAnalytics {
  mode: TradingMode;
  /** Per trade date: gross, fees, net, count — the calendar heatmap's cells. */
  calendar: Array<{ date: string; net: number; trades: number }>;
  nlv: Array<{ date: string; nlv: number }>;
  byDelta: MeicBreakdownRow[];
  byWing: MeicBreakdownRow[];
  bySymbol: MeicBreakdownRow[];
  byWeekday: MeicBreakdownRow[];
  byHour: MeicBreakdownRow[];
}

export function readMeicDeepAnalytics(
  config: ConsoleConfig,
  mode: TradingMode,
  scope: MeicScopeFilter = NO_SCOPE,
): MeicDeepAnalytics {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicDeepAnalytics = { mode, calendar: [], nlv: [], byDelta: [], byWing: [], bySymbol: [], byWeekday: [], byHour: [] };
  return withReadOnlyDb<MeicDeepAnalytics>(dbPath, empty, (db) => {
    const sc = scopeSql(db, scope);
    const calendar = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, SUM(pnl) - SUM(COALESCE(fees, 0)) AS net, COUNT(*) AS trades
           FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL${sc.and} GROUP BY trade_date ORDER BY trade_date`,
      )
      .all(...sc.params)
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
        .prepare<string[], Record<string, unknown>>(
          `SELECT ${bucketExpr} AS bucket, COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
                  SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                  AVG(pnl - COALESCE(fees, 0)) AS avg_net
             FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL${sc.and}
            GROUP BY bucket ORDER BY bucket`,
        )
        .all(...sc.params)
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
      // Entry hour, read off the stored ET timestamp — already ET, whether it
      // carries an explicit -04:00/-05:00 offset or an "ET"/"EDT" suffix, so no
      // conversion applies. Rows outside 09:00-16:00 ET are replay and
      // shakedown runs rather than session entries; the era filter drops them
      // from the default view and the marker keeps them readable under era ALL.
      byHour: breakdown(
        `CASE WHEN ${ENTRY_HOUR} IS NULL THEN 'unknown'
              ELSE ${ENTRY_HOUR} || ':00-' ||
                   printf('%02d', CAST(${ENTRY_HOUR} AS INTEGER) + 1) || ':00' ||
                   CASE WHEN CAST(${ENTRY_HOUR} AS INTEGER) BETWEEN 9 AND 15
                        THEN '' ELSE ' *' END END`,
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

export function readMeicAnalytics(
  config: ConsoleConfig,
  mode: TradingMode,
  scope: MeicScopeFilter = NO_SCOPE,
): MeicAnalytics {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicAnalytics = {
    mode,
    periods: [],
    exitReasons: [],
    feeDrag: { grossCredit: 0, fees: 0, netPnl: 0, dragPct: null },
  };
  return withReadOnlyDb<MeicAnalytics>(dbPath, empty, (db) => {
    const sc = scopeSql(db, scope);
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
    const periodStmt = db.prepare<string[], Record<string, unknown>>(
      `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS trades,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0 THEN 1 ELSE 0 END) AS losses
         FROM ic_trades WHERE ${RESOLVED} AND trade_date >= ?${sc.and}`,
    );
    const allStmt = db.prepare<string[], Record<string, unknown>>(
      `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS trades,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0 THEN 1 ELSE 0 END) AS losses
         FROM ic_trades WHERE ${RESOLVED}${sc.and}`,
    );
    const periods = starts.map(([label, start]) => {
      const r = (start === null ? allStmt.get(...sc.params) : periodStmt.get(start, ...sc.params)) ?? {};
      return {
        label,
        net: Number(r["net"] ?? 0),
        trades: Number(r["trades"] ?? 0),
        wins: Number(r["wins"] ?? 0),
        losses: Number(r["losses"] ?? 0),
      };
    });

    const exitReasons = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COALESCE(exit_reason, 'open') AS reason, COUNT(*) AS count
           FROM ic_trades WHERE ${RESOLVED}${sc.and} GROUP BY COALESCE(exit_reason, 'open') ORDER BY count DESC`,
      )
      .all(...sc.params)
      .map((r) => ({ reason: String(r["reason"]), count: Number(r["count"]) }));

    const fd = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COALESCE(SUM(net_credit * COALESCE(quantity, 1) * 100), 0) AS gross,
                COALESCE(SUM(fees), 0) AS fees, COALESCE(SUM(pnl), 0) AS net
           FROM ic_trades WHERE ${RESOLVED}${sc.and}`,
      )
      .get(...sc.params) ?? {};
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
