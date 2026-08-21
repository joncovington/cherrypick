import path from "node:path";
import type { MeicDivergence, MeicPayload, MeicTradeRow, MeicSummaryRow, Paged, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import type { DatabaseHandle } from "./db.js";
import { withReadOnlyDb, hasColumn, hasTable, num, str } from "./db.js";
import { emptyPage, pagedQuery, FIRST_PAGE, type PageRequest } from "./paging.js";
import { payoffAt, type Leg } from "../analytics/payoff.js";
import { equityCurve, periodKey, riskSummary, stdev } from "../analytics/riskMetrics.js";


/**
 * The era the module counts as evidence. Its own analytics narrow to this by
 * default, so anything tagged to an earlier era is bring-up and shakedown data
 * — mixing it in silently distorts every breakdown. Duplicated as a literal
 * rather than imported so the packages stay decoupled; kept in step with
 * `CURRENT_ERA` in `packages/meic/.../analytics.py`.
 */
export const CURRENT_ERA = "advisor";

/**
 * Era date spans, for tables WITHOUT an era column (`daily_summary` is a per-day aggregate the
 * module writes with no era tag). Facts mirrored from the module's journaled measurement breaks —
 * the four-stream sample ran 2026-08-07..2026-08-20 and the advisor era opened 2026-08-21 — under
 * the same hand-sync contract as `CURRENT_ERA` above. An era key not listed here widens rather
 * than inventing a span.
 */
const ERA_DATES: Record<string, { from: string | null; to: string | null }> = {
  book: { from: null, to: "2026-08-06" },
  sample: { from: "2026-08-07", to: "2026-08-20" },
  advisor: { from: "2026-08-21", to: null },
};

/** WHERE fragment bounding a date column to the scope's era, `null` when unbounded ("ALL"). */
function eraDateSql(era: string | null, column: string): { sql: string | null; params: string[] } {
  const key = era ?? CURRENT_ERA;
  if (key === "ALL") return { sql: null, params: [] };
  const span = ERA_DATES[key];
  if (span === undefined) return { sql: null, params: [] };
  const parts: string[] = [];
  const params: string[] = [];
  if (span.from !== null) {
    parts.push(`${column} >= ?`);
    params.push(span.from);
  }
  if (span.to !== null) {
    parts.push(`${column} <= ?`);
    params.push(span.to);
  }
  return { sql: parts.length > 0 ? parts.join(" AND ") : null, params };
}

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

export type MeicOutcome = "all" | "wins" | "losses" | "open";

/** The trade log's own query: page-wide scope, plus its filters and its page. */
export interface MeicTradeQuery extends MeicScopeFilter, PageRequest {
  /**
   * The session this log is scoped to; null resolves to the latest, exactly as the forest and
   * occupancy cards resolve it. The log sits under a tab called "today" and used to answer for the
   * whole era instead — the same confusion the flies books had.
   */
  day: string | null;
  outcome: MeicOutcome;
  /** Exit reason as the analytics card labels it — "open" means no exit reason yet. */
  reason: string | null;
  search: string;
}

export const NO_TRADE_QUERY: MeicTradeQuery = {
  ...NO_SCOPE,
  ...FIRST_PAGE,
  day: null,
  outcome: "all",
  reason: null,
  search: "",
};

/**
 * The log's filters run in SQL rather than over the fetched page. Filtering a
 * single page would make the match count a statement about that page and not
 * about the data — "3 losses" when the scope holds two hundred.
 */
function tradeFilterSql(db: DatabaseHandle, q: MeicTradeQuery): { where: string; params: string[] } {
  const sc = scopeSql(db, q);
  const clauses = ["1=1"];
  const params = [...sc.params];
  if (sc.and !== "") clauses.push(sc.and.slice(5));
  // Resolved within the same scope the forest uses, so the two cards can never name different days.
  const day =
    q.day ??
    db
      .prepare<string[], { d: string | null }>(`SELECT MAX(trade_date) AS d FROM ic_trades WHERE 1=1${sc.and}`)
      .get(...sc.params)?.d ??
    null;
  if (day !== null) {
    clauses.push("trade_date = ?");
    params.push(day);
  }
  if (q.outcome === "wins") clauses.push("pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0");
  if (q.outcome === "losses") clauses.push("pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0");
  if (q.outcome === "open") clauses.push("pnl IS NULL");
  if (q.reason !== null) {
    clauses.push("COALESCE(exit_reason, 'open') = ?");
    params.push(q.reason);
  }
  if (q.search !== "") {
    // Same fields the log renders, so what you see is what you search.
    clauses.push(
      `(trade_date LIKE ? OR symbol LIKE ? OR status LIKE ? OR COALESCE(exit_reason, '') LIKE ?)`,
    );
    const like = `%${q.search.replace(/[%_]/g, "")}%`;
    params.push(like, like, like, like);
  }
  return { where: clauses.join(" AND "), params };
}

export function readMeic(config: ConsoleConfig, mode: TradingMode, query: MeicTradeQuery = NO_TRADE_QUERY): MeicPayload {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const trades = withReadOnlyDb<Paged<MeicTradeRow>>(dbPath, emptyPage(query), (db) => {
    const f = tradeFilterSql(db, query);
    return pagedQuery<MeicTradeRow>(
      db,
      {
        columns: `id, trade_date, entry_time, symbol, put_strike, call_strike, wing_width,
                  net_credit, quantity, status, pnl, fees, exit_reason, iv_rank_at_entry`,
        from: "ic_trades",
        where: f.where,
        params: f.params,
        orderBy: "id DESC",
      },
      query,
      (r) => ({
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
      }),
    );
  });

  // Bounded to the same era as the trade log above it — daily_summary has no era column, so the
  // bound is the era's DATE span; pre-cutover rows aggregate the retired arms and answering with
  // them beside an era-scoped log was the mismatch the 2026-08-21 report caught.
  const eraSql = eraDateSql(query.era, "summary_date");
  const summaries = withReadOnlyDb<MeicSummaryRow[]>(dbPath, [], (db) =>
    db
      .prepare<string[], Record<string, unknown>>(
        `SELECT summary_date, symbol, total_entries, entries_filled, entries_stopped,
                net_pnl, win_rate_pct
           FROM daily_summary${eraSql.sql !== null ? ` WHERE ${eraSql.sql}` : ""}
          ORDER BY summary_date DESC LIMIT 20`,
      )
      .all(...eraSql.params)
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


export interface MeicScope {
  symbols: string[];
  profiles: string[];
  /** Every era present, with its row count, so the page can say what a filter costs. */
  eras: Array<{ era: string; trades: number }>;
  currentEra: string;
}

/**
 * The page-wide scope selects' own options.
 *
 * `symbols` and `profiles` are narrowed to the SAME era the data is, because a select that offers
 * more than the scope can return is lying about what is reachable. Measured on the paper ledger:
 * the unfiltered profile list carries 14 names while the current era holds 3 -- the retired ladder
 * tiers, the GEX study pair, and the pre-2026-07-18 symbol/wing cells are all still in the table by
 * design, since this module retires an arm by writing a verdict rather than deleting its rows. Eleven
 * of those fourteen options selected nothing, and an option that yields no rows reads as "this arm
 * did nothing" rather than "this arm is not in this era". Symbols are the same story: 6 all-time
 * against 1 in the current era.
 *
 * `eras` is deliberately NOT narrowed -- it is the list you widen WITH, and filtering it by the
 * current era would leave no way back out.
 */
export function readMeicScope(
  config: ConsoleConfig,
  mode: TradingMode,
  era: string | null = null,
): MeicScope {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicScope = { symbols: [], profiles: [], eras: [], currentEra: CURRENT_ERA };
  return withReadOnlyDb<MeicScope>(dbPath, empty, (db) => {
    const scoped = era !== "ALL" && hasColumn(db, "ic_trades", "era");
    const and = scoped ? " AND era = ?" : "";
    const params: string[] = scoped ? [era ?? CURRENT_ERA] : [];
    return {
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
        .prepare<string[], { s: string }>(
          `SELECT DISTINCT symbol AS s FROM ic_trades WHERE symbol IS NOT NULL${and} ORDER BY symbol`,
        )
        .all(...params)
        .map((r) => r.s),
      profiles: db
        .prepare<string[], { p: string }>(
          `SELECT DISTINCT risk_profile AS p FROM ic_trades
            WHERE risk_profile IS NOT NULL${and} ORDER BY risk_profile`,
        )
        .all(...params)
        .map((r) => r.p),
    };
  });
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

    // --- daily equity + drawdown, NET OF FEES ---
    // These two read gross `SUM(pnl)` while the calendar beside them read net, so one MEIC page
    // showed two different curves both labelled net. Net-of-fees is the module-wide definition
    // (core.ledgers, flies, and the calendar below all use it), and fees are a real drag on this
    // strategy -- a gross equity curve overstates every drawdown recovery.
    const dailyRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, COALESCE(SUM(pnl - COALESCE(fees, 0)), 0) AS net FROM ic_trades WHERE ${where}
          GROUP BY trade_date ORDER BY trade_date`,
      )
      .all(...params);
    const equity = equityCurve(
      dailyRows.map((r) => ({ date: String(r["trade_date"]), net: Number(r["net"]) })),
    );

    // --- risk-adjusted metrics from the DAILY series regardless of display granularity ---
    const risk = riskSummary(equity);

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
              `SELECT risk_profile, trade_date, COALESCE(SUM(pnl - COALESCE(fees, 0)), 0) AS net FROM ic_trades
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

    const nlvEra = eraDateSql(scope.era, "summary_date");
    const nlv = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT summary_date, closing_nlv FROM daily_summary
          WHERE closing_nlv IS NOT NULL${nlvEra.sql !== null ? ` AND ${nlvEra.sql}` : ""}
          ORDER BY summary_date`,
      )
      .all(...nlvEra.params)
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
  /** TODAY / WEEK / MONTH / YEAR / ALL. Net is after fees, as everywhere else in the suite. */
  periods: Array<{ label: string; net: number; trades: number; wins: number; losses: number }>;
  /**
   * Today's result per profile — which arm actually made the money, the question the page could
   * not answer. The Performance tab's per-profile table is cumulative and states that it ignores
   * the page's scope, so it never covered this.
   */
  byProfile: Array<{ profile: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  /** Today's fee drag per profile, same grouping as byProfile.
   *
   * `gross` here is premium COLLECTED, not gross P&L -- it does not reconcile with `net` the way
   * flies' identically-named field does. Renaming the field would ripple through the page; the
   * column it renders is labelled "credit" instead. */
  profileFeeDrag: Array<{ profile: string; gross: number; fees: number; net: number; dragPct: number | null }>;
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
    byProfile: [],
    profileFeeDrag: [],
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
    // Net is `pnl - fees`. `ic_trades.pnl` is gross of fees, so summing it alone reported a
    // different "net" from the deep-analytics calendar right beside it on the same page, and from
    // this very query's own win/loss test, which has always been fee-adjusted. Fees are a real cost
    // of the trade and every other net in the suite subtracts them.
    //
    // Subtracted INSIDE the sum on purpose. RESOLVED only excludes cancelled/pending entries, so
    // mid-session it still admits 0DTE rows that have not settled and carry a NULL pnl. Summing the
    // two columns separately would charge those rows' fees against a P&L they have not earned yet
    // and report every profile down by exactly its fees — a number that looks like a result. With
    // the subtraction inside, an unsettled row is NULL and drops out of the sum entirely.
    const NET = "COALESCE(SUM(pnl - COALESCE(fees, 0)), 0)";
    // The rows that actually contribute to NET, so an average has the same denominator as its total.
    const SETTLED = "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END)";
    const WINS = "SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END)";
    const LOSSES = "SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0 THEN 1 ELSE 0 END)";
    const periodStmt = db.prepare<string[], Record<string, unknown>>(
      `SELECT ${NET} AS net, COUNT(*) AS trades, ${WINS} AS wins, ${LOSSES} AS losses
         FROM ic_trades WHERE ${RESOLVED} AND trade_date >= ?${sc.and}`,
    );
    const allStmt = db.prepare<string[], Record<string, unknown>>(
      `SELECT ${NET} AS net, COUNT(*) AS trades, ${WINS} AS wins, ${LOSSES} AS losses
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

    // TODAY, per profile. Scoped to the same session the tiles above it report, so the page does
    // not put "this year" and "today" beside each other under one heading — the mistake the flies
    // reader already carries a note about.
    const profileRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT risk_profile AS profile, ${SETTLED} AS trades,
                ${NET} AS net, ${WINS} AS wins, ${LOSSES} AS losses,
                COALESCE(SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN pnl - COALESCE(fees, 0) ELSE 0 END), 0) AS won,
                COALESCE(SUM(CASE WHEN pnl - COALESCE(fees, 0) < 0 THEN COALESCE(fees, 0) - pnl ELSE 0 END), 0) AS lost
           FROM ic_trades WHERE ${RESOLVED} AND trade_date = ?${sc.and}
          GROUP BY risk_profile HAVING trades > 0 ORDER BY net DESC`,
      )
      .all(today, ...sc.params);
    const byProfile = profileRows.map((r) => {
      const trades = Number(r["trades"]);
      const wins = Number(r["wins"]);
      const losses = Number(r["losses"]);
      const lost = Number(r["lost"]);
      const net = Number(r["net"]);
      return {
        profile: String(r["profile"] ?? "?"),
        trades,
        net,
        winPct: wins + losses > 0 ? (wins / (wins + losses)) * 100 : null,
        avg: trades > 0 ? net / trades : null,
        profitFactor: lost > 0 ? Number(r["won"]) / lost : null,
      };
    });

    const profileFeeDrag = db
      .prepare<string[], Record<string, unknown>>(
        // Restricted to settled rows so gross, fees and net all describe the same trades — a drag
        // percentage mixing an unsettled row's fees into a settled row's credit means nothing.
        `SELECT risk_profile AS profile,
                COALESCE(SUM(net_credit * COALESCE(quantity, 1) * 100), 0) AS gross,
                COALESCE(SUM(fees), 0) AS fees, ${NET} AS net
           FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL AND trade_date = ?${sc.and}
          GROUP BY risk_profile ORDER BY risk_profile`,
      )
      .all(today, ...sc.params)
      .map((r) => {
        const gross = Number(r["gross"]);
        const fees = Number(r["fees"]);
        return {
          profile: String(r["profile"] ?? "?"),
          gross,
          fees,
          net: Number(r["net"]),
          dragPct: Math.abs(gross) > 0 ? (fees / Math.abs(gross)) * 100 : null,
        };
      });

    // Grouped on the LEG PAIR, not on ic_trades.exit_reason.
    //
    // That column carries only two values -- 'expired_settlement' and 'stopped+expired_settlement'
    // -- and the second is three different outcomes wearing one label. In the sample era its 873
    // rows are 637 put-stopped, 161 call-stopped and 75 where BOTH sides stopped, and those are not
    // variations of each other: a single-side stop is the design working (eat one side, the other
    // expires worthless) and averages about -$15, while a double stop means price crossed both
    // short strikes in one session, paying to close both and collecting nothing. It averages
    // -$149. Seventy-five trades, 4.5% of the era, carry 47% of every stop-related dollar lost --
    // and the card that was supposed to show exits could not distinguish them at all.
    //
    // `analytics.break_even` in the module already reads the pair for exactly this reason ("stopped
    // at the IC level also covers a single-side stop, the designed scratch"), so this uses the same
    // definition rather than inventing a second one that could disagree with the arm scorecard.
    const exitReasons = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT CASE
                  WHEN put_status = 'stopped' AND call_status = 'stopped' THEN 'both sides stopped'
                  WHEN put_status = 'stopped' THEN 'put side stopped'
                  WHEN call_status = 'stopped' THEN 'call side stopped'
                  WHEN put_status IS NULL AND call_status IS NULL THEN COALESCE(exit_reason, 'open')
                  ELSE 'expired clean'
                END AS reason,
                COUNT(*) AS count
           FROM (SELECT t.ic_order_id, t.exit_reason,
                        MAX(CASE WHEN l.side = 'put' THEN l.status END) AS put_status,
                        MAX(CASE WHEN l.side = 'call' THEN l.status END) AS call_status
                   FROM (SELECT * FROM ic_trades WHERE ${RESOLVED}${sc.and}) t
                   LEFT JOIN ic_spread_legs l ON l.ic_order_id = t.ic_order_id
                  GROUP BY t.ic_order_id)
          GROUP BY reason ORDER BY count DESC`,
      )
      .all(...sc.params)
      .map((r) => ({ reason: String(r["reason"]), count: Number(r["count"]) }));

    const fd = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COALESCE(SUM(net_credit * COALESCE(quantity, 1) * 100), 0) AS gross,
                COALESCE(SUM(fees), 0) AS fees, ${NET} AS net
           FROM ic_trades WHERE ${RESOLVED}${sc.and}`,
      )
      .get(...sc.params) ?? {};
    const gross = Number(fd["gross"] ?? 0);
    const fees = Number(fd["fees"] ?? 0);
    return {
      mode,
      periods,
      byProfile,
      profileFeeDrag,
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

// ── The MEIC profit forest ──────────────────────────────────────────────────

export interface MeicForestArm {
  profile: string;
  /** One entry per open IC, so a nested condor reads as nested rather than as one lumpy line. */
  positions: Array<{ icOrderId: string; putStrike: number; callStrike: number; wingWidth: number; netCredit: number; quantity: number }>;
  /** The arm's aggregate curve: P&L at expiry across the price grid. */
  prices: number[];
  pnl: number[];
  /** Each position's own curve, drawn faintly behind the aggregate. */
  perPosition: Array<{ icOrderId: string; pnl: number[] }>;
  /**
   * What actually became of these trades, and what they actually made (net of fees).
   *
   * The as-entered curve prices every trade as if it were held to expiry, which for MEIC is the one
   * thing that reliably did NOT happen — stop management is the strategy, and on a normal session
   * most of the book comes off before settlement. Without these counts beside it, the curve's wing
   * losses read as risk the book ran rather than as risk the stops existed to prevent.
   */
  outcome: { entered: number; stopped: number; expired: number; open: number; realisedNet: number };
}

export interface MeicForest {
  mode: TradingMode;
  tradeDate: string | null;
  symbol: string | null;
  /** How many trades this day holds, and how many are still open. A MEIC book resolves entirely at
   *  settlement, so after 16:00 every trade is stopped or expired and an expiry-payoff curve has
   *  nothing left to describe — the card must say that rather than render an empty chart. */
  tradesToday: number;
  openPositions: number;
  /** The day's book drawn at ENTRY geometry, so a settled session still shows the structure it
   *  accumulated. Explicitly not a claim about outcome: a stopped side came off before expiry and
   *  its realized P&L is not this curve. */
  asEntered: MeicForestArm[];
  arms: MeicForestArm[];
  /** Strikes released by a stop today: drawn as vacated so the forest and the
   *  occupancy map can never disagree about what the book still holds. */
  releasedStrikes: Array<{ profile: string; strike: number; right: "P" | "C"; at: string | null }>;
  lastSpot: number | null;
}

/**
 * An IC as generic payoff legs. Long strikes are DERIVED from `wing_width` —
 * the ledger stores only the shorts as numbers (the longs live inside the
 * OCC strings) — mirroring `paper.ic_legs` in the MEIC package. Kept as one
 * conversion here for the same reason it is one function there: the same
 * arithmetic in three places is three chances to disagree about what is held.
 *
 * `price` is carried entirely on the short put so the curve sits at the trade's
 * real net credit; splitting it across four legs would change nothing about the
 * shape and would invent per-leg prices the ledger never recorded.
 */
function icLegs(row: {
  putStrike: number;
  callStrike: number;
  wingWidth: number;
  netCredit: number;
}): Leg[] {
  return [
    { kind: "put", quantity: -1, price: row.netCredit, strike: row.putStrike },
    { kind: "put", quantity: 1, price: 0, strike: row.putStrike - row.wingWidth },
    { kind: "call", quantity: -1, price: 0, strike: row.callStrike },
    { kind: "call", quantity: 1, price: 0, strike: row.callStrike + row.wingWidth },
  ];
}

/** The price grid the forest is drawn on: every strike in play plus a margin either side. */
function forestGrid(strikes: number[]): number[] {
  if (strikes.length === 0) return [];
  const lo = Math.min(...strikes);
  const hi = Math.max(...strikes);
  const pad = Math.max((hi - lo) * 0.35, hi * 0.01);
  const start = lo - pad;
  const step = (hi + pad - start) / 120;
  return Array.from({ length: 121 }, (_, i) => Math.round((start + i * step) * 100) / 100);
}

/**
 * Per-profile expiry-payoff curves for the open book on one day — MEIC's
 * equivalent of the flies profit forest.
 *
 * Two things this needs that the flies forest does not. **Nesting is the
 * point**: MEIC stacks condors inside one another deliberately, so each IC's
 * own curve is returned alongside the aggregate rather than only the sum.
 * **Stops change the shape mid-day**: a stopped side releases its strikes, so
 * those are reported explicitly — otherwise the forest and the strike-occupancy
 * map disagree about what is held and neither gets trusted.
 */
export function readMeicForest(
  config: ConsoleConfig,
  mode: TradingMode,
  day: string | null,
  scope: MeicScopeFilter = NO_SCOPE,
): MeicForest {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicForest = {
    mode,
    tradeDate: null,
    symbol: null,
    tradesToday: 0,
    openPositions: 0,
    asEntered: [],
    arms: [],
    releasedStrikes: [],
    lastSpot: null,
  };

  return withReadOnlyDb<MeicForest>(dbPath, empty, (db) => {
    const { and, params: scopeParams } = scopeSql(db, scope);
    const dayRow = day
      ? { d: day }
      : db
          .prepare<string[], { d: string }>(
            `SELECT MAX(trade_date) AS d FROM ic_trades WHERE 1=1${and}`,
          )
          .get(...scopeParams);
    const tradeDate = dayRow?.d ?? null;
    if (tradeDate === null) return empty;

    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ic_order_id, risk_profile, symbol, put_strike, call_strike, wing_width,
                net_credit, quantity, status, underlying_price_entry,
                pnl, COALESCE(fees, 0) AS fees
           FROM ic_trades
          WHERE trade_date = ?${and}`,
      )
      .all(tradeDate, ...scopeParams);

    // Open positions build the live curves; the whole day builds the as-entered view.
    const open = rows.filter((r) => str(r["status"]) === "open");
    const entered = rows.filter((r) => str(r["status"]) !== "cancelled");
    // One builder, run over whichever row set is being drawn. The grid is derived from the SAME
    // rows as the curves — a grid built from the open book while the curves came from the whole day
    // would clip the as-entered view to whatever happened to still be open.
    const buildArms = (source: Array<Record<string, unknown>>): MeicForestArm[] => {
      if (source.length === 0) return [];
      const byProfile = new Map<string, Array<Record<string, unknown>>>();
      for (const r of source) {
        const profile = str(r["risk_profile"]) ?? "?";
        const list = byProfile.get(profile) ?? [];
        list.push(r);
        byProfile.set(profile, list);
      }
      const prices = forestGrid(
        source.flatMap((r) => {
          const w = num(r["wing_width"]) ?? 0;
          const p = num(r["put_strike"]) ?? 0;
          const c = num(r["call_strike"]) ?? 0;
          return [p - w, p, c, c + w];
        }),
      );
      return [...byProfile.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([profile, list]) => {
          const positions = list.map((r) => ({
            icOrderId: str(r["ic_order_id"]) ?? "",
            putStrike: num(r["put_strike"]) ?? 0,
            callStrike: num(r["call_strike"]) ?? 0,
            wingWidth: num(r["wing_width"]) ?? 0,
            netCredit: num(r["net_credit"]) ?? 0,
            quantity: num(r["quantity"]) ?? 1,
          }));
          const perPosition = positions.map((pp) => ({
            icOrderId: pp.icOrderId,
            pnl: prices.map((sp) => payoffAt(icLegs(pp), sp) * pp.quantity),
          }));
          const pnl = prices.map((_, i) => perPosition.reduce((sum, pp) => sum + (pp.pnl[i] ?? 0), 0));
          // What these trades actually did, beside what the curve says they would have done.
          const statusOf = (r: Record<string, unknown>) => str(r["status"]) ?? "";
          const outcome = {
            entered: list.length,
            stopped: list.filter((r) => statusOf(r) === "stopped").length,
            expired: list.filter((r) => statusOf(r) === "expired").length,
            open: list.filter((r) => statusOf(r) === "open").length,
            // pnl - fees, the same net every other MEIC surface reports.
            realisedNet: list.reduce(
              (sum, r) => sum + (num(r["pnl"]) === null ? 0 : (num(r["pnl"]) ?? 0) - (num(r["fees"]) ?? 0)),
              0,
            ),
          };
          return { profile, positions, prices, pnl, perPosition, outcome };
        });
    };

    const arms = buildArms(open);
    const asEntered = buildArms(entered);

    // A stopped side has released its strikes. Reported per side rather than per
    // trade: MEIC stops each side independently, so a trade can have given back
    // its calls while its puts are still on the book.
    // Deduped: a settled session resolves every trade, and emitting a row per strike per trade put
    // 2,840 entries in this array for one day. What a reader needs is the SET of strikes a stop
    // handed back, per arm and side, not one entry per trade that touched them.
    const releasedKeys = new Set<string>();
    const released: MeicForest["releasedStrikes"] = [];
    const pushReleased = (profile: string, strike: number, right: "P" | "C") => {
      const key = `${profile}|${right}|${strike}`;
      if (releasedKeys.has(key)) return;
      releasedKeys.add(key);
      released.push({ profile, strike, right, at: null });
    };
    for (const r of rows) {
      const status = str(r["status"]);
      if (status === "open") continue;
      const profile = str(r["risk_profile"]) ?? "?";
      const w = num(r["wing_width"]) ?? 0;
      const p = num(r["put_strike"]);
      const c = num(r["call_strike"]);
      if (p !== null) {
        pushReleased(profile, p, "P");
        if (w) pushReleased(profile, p - w, "P");
      }
      if (c !== null) {
        pushReleased(profile, c, "C");
        if (w) pushReleased(profile, c + w, "C");
      }
    }

    const symbol = str(open[0]?.["symbol"] ?? rows[0]?.["symbol"]) ?? null;
    const lastSpot = num(rows[rows.length - 1]?.["underlying_price_entry"]);
    return {
      mode,
      tradeDate,
      symbol,
      tradesToday: rows.length,
      openPositions: open.length,
      asEntered,
      arms,
      releasedStrikes: released,
      lastSpot,
    };
  });
}

/**
 * Profile divergence: how often MEIC's arms reached DIFFERENT entry decisions on the same tick.
 *
 * The flies page has had this since week one and the reasoning carries over unchanged: an
 * experiment can only separate two arms to the extent they actually disagree, and a pair agreeing
 * above 80% cannot answer the question as framed no matter how long it runs. Far better learned in
 * week one than in month three.
 *
 * MEIC's arms differ in GATES rather than in centring, so the thing to compare is the outcome each
 * profile reached, not a strike. `entry_attempts` is the right table because it records one
 * uncollapsed row per (profile x symbol) per tick INCLUDING refusals — the arms that matter most
 * here are the ones that go dark on a low-IV day, and a table of fills alone cannot see them.
 *
 * Bucketed on (ts, symbol): the loop stamps every profile in one tick with the same HH:MM, so a
 * tick is directly comparable across arms.
 */
export function readMeicDivergence(config: ConsoleConfig, mode: TradingMode, day: string | null): MeicDivergence {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicDivergence = { date: null, ticks: 0, allAgreeRatePct: null, pairs: [], outcomes: [] };
  return withReadOnlyDb<MeicDivergence>(dbPath, empty, (db) => {
    if (!hasTable(db, "entry_attempts")) return empty;
    const date =
      day ??
      db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM entry_attempts").get()?.d ??
      null;
    if (date === null) return empty;

    const rows = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT ts, symbol, risk_profile, outcome FROM entry_attempts WHERE trade_date = ? ORDER BY ts",
      )
      .all(date);

    const ticks = new Map<string, Record<string, string>>();
    const outcomeCounts = new Map<string, number>();
    for (const r of rows) {
      const profile = str(r["risk_profile"]);
      const outcome = str(r["outcome"]);
      if (profile === null || outcome === null) continue;
      outcomeCounts.set(outcome, (outcomeCounts.get(outcome) ?? 0) + 1);
      const key = `${String(r["ts"])}|${String(r["symbol"])}`;
      let bucket = ticks.get(key);
      if (bucket === undefined) {
        bucket = {};
        ticks.set(key, bucket);
      }
      bucket[profile] = outcome;
    }

    const pairs = new Map<string, boolean[]>();
    let allAgree = 0;
    let considered = 0;
    for (const outcomes of ticks.values()) {
      const profiles = Object.keys(outcomes).sort();
      if (profiles.length < 2) continue; // agreement is undefined for one arm
      considered += 1;
      if (new Set(Object.values(outcomes)).size === 1) allAgree += 1;
      for (let i = 0; i < profiles.length; i++) {
        for (let j = i + 1; j < profiles.length; j++) {
          const key = `${profiles[i]} vs ${profiles[j]}`;
          let list = pairs.get(key);
          if (list === undefined) {
            list = [];
            pairs.set(key, list);
          }
          list.push(outcomes[profiles[i]!] === outcomes[profiles[j]!]);
        }
      }
    }

    return {
      date,
      ticks: considered,
      allAgreeRatePct: considered > 0 ? (allAgree / considered) * 100 : null,
      pairs: [...pairs.entries()]
        .map(([profiles, matches]) => ({
          profiles,
          ticks: matches.length,
          agreementRatePct: matches.length > 0 ? (matches.filter(Boolean).length / matches.length) * 100 : null,
        }))
        .sort((a, b) => (b.agreementRatePct ?? 0) - (a.agreementRatePct ?? 0)),
      outcomes: [...outcomeCounts.entries()]
        .map(([outcome, count]) => ({ outcome, count }))
        .sort((a, b) => b.count - a.count),
    };
  });
}
