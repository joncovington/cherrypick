import { hasRange, rangeClauses } from "./dateRange.js";
import { concededArmMoney, entryOutcomes } from "./armCosts.js";
import { feeDragPct, sumIfAllRecorded, type ArmCostsPayload } from "@console/shared";
import path from "node:path";
import type { MeicDivergence, MeicPayload, MeicTradeRow, MeicTradeTotals, MeicSummaryRow, MeicSummarySlice, Paged, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import type { DatabaseHandle } from "./db.js";
import { readOnlyDb, withReadOnlyDb, withReadSnapshot, hasColumn, hasTable, num, str, armColumnOf, findArmColumn } from "./db.js";
import { readMeasurementBreaks, readSchemaDrift } from "./integrity.js";
import { emptyPage, pagedQuery, FIRST_PAGE, type PageRequest, type SortAccessors } from "./paging.js";
import { payoffAt, type Leg } from "../analytics/payoff.js";
import { summaryWindow } from "./summary/common.js";
import { cachedQuote } from "./streamcache.js";
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
    clauses.push(`${armColumnOf(db, "ic_trades")} = ?`);
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
  /**
   * Inclusive trade-date bounds (2026-09-25). Either one set REPLACES the one-session scope above:
   * the history table's date range asks "across these days", which the header's day cannot answer.
   */
  from?: string | null;
  to?: string | null;
  outcome: MeicOutcome;
  /** `positions`: every trade the session held (cancelled entries never were). `history`: the ones
   *  that have closed. Two pages of the same query, so they can never disagree about a row. */
  view: MeicTradeView;
  /** Exit reason as the analytics card labels it — "open" means no exit reason yet. */
  reason: string | null;
  search: string;
}

export const NO_TRADE_QUERY: MeicTradeQuery = {
  ...NO_SCOPE,
  ...FIRST_PAGE,
  day: null,
  outcome: "all",
  view: "positions",
  reason: null,
  search: "",
};

export type MeicTradeView = "positions" | "history";

/**
 * Where "the loop ran this session" is written, each as (table, date column). "Latest session" is
 * the newest date across all of them -- the loop's last RUN, not the trade log's last ROW.
 *
 * It was `MAX(trade_date)` over `ic_trades`, which cannot name a session with no trades. On
 * 2026-09-30 every paper arm was refused all day (a quarterly-expiry morning, then the call-side
 * floor), so the session page showed 2026-09-29's 538 trades beside 2026-09-30's 3,128 refusals --
 * the attempts reader resolves its own day off `entry_attempts` -- and the live page showed the
 * live ledger's last trade, from 2026-06-30, under the same "latest session" label as today.
 * pmcc's `resolvePmccSession` is the same fix for the same shape.
 */
const MEIC_SESSION_SOURCES: ReadonlyArray<readonly [table: string, column: string]> = [
  ["daily_summary", "summary_date"],
  ["loop_log", "loop_date"],
  ["entry_attempts", "trade_date"],
  ["ic_trades", "trade_date"],
];

/** A date that cannot match any row: a THROWN resolve narrows to nothing rather than widening to
 * the era (readers/flies.ts' UNRESOLVABLE_DAY, same reasoning). */
const UNRESOLVABLE_DAY = "unresolved";

function latestMeicSessionIn(db: DatabaseHandle, era: string | null = null): string | null {
  // Only what this ledger has: a live book, an older paper book and a fixture differ in which
  // journal tables exist, and naming a missing one throws.
  const usable = MEIC_SESSION_SOURCES.filter(([t, c]) => hasTable(db, t) && hasColumn(db, t, c));
  if (usable.length === 0) return null;
  // Bound each source to the scope's era, so the resolved default day is the LATEST session IN
  // THAT ERA. Without it, picking an older era with no session chosen named the current era's
  // latest day, which has no rows in the selected era and drew an empty card (2026-10-06).
  // `eraDateSql` widens to unbounded for "ALL" and for an unknown key, matching `scopeSql`. Only a
  // ledger that carries the era column has eras at all -- an older `risk_profile`-only book must
  // keep resolving its latest session unbounded, exactly as it did before.
  const hasEras = hasColumn(db, "ic_trades", "era");
  const parts: string[] = [];
  const params: string[] = [];
  for (const [t, c] of usable) {
    const e = hasEras ? eraDateSql(era, c) : { sql: null, params: [] };
    parts.push(`SELECT MAX(${c}) AS d FROM ${t}${e.sql !== null ? ` WHERE ${e.sql}` : ""}`);
    params.push(...e.params);
  }
  const latest =
    db
      .prepare<string[], { d: string | null }>(`SELECT MAX(d) AS d FROM (${parts.join(" UNION ALL ")})`)
      .get(...params)?.d ?? null;
  // A CLOSED era ends at its own last stamped trade. Its date span runs to the next era's start, so
  // the journals alone named a no-trade tail day: the book era's span ends 2026-08-06, its last
  // trade is 2026-08-04, and the loop ran 08-05 and 08-06 with every arm blocked -- so era=book with
  // no day showed 0 trades (with a range, 290), on a day no card has anything to say about (the
  // attempts table did not exist yet). The open era keeps the journals' answer: today may have no
  // trades yet, and a refused session is still a session (the 2026-09-30 rule above).
  const key = era ?? CURRENT_ERA;
  if (hasEras && key !== "ALL" && ERA_DATES[key]?.to != null) {
    const last =
      db
        .prepare<[string], { d: string | null }>("SELECT MAX(trade_date) AS d FROM ic_trades WHERE era = ?")
        .get(key)?.d ?? null;
    if (last !== null && (latest === null || last < latest)) return last;
  }
  return latest;
}

function meicDbPath(config: ConsoleConfig, mode: TradingMode): string {
  return path.join(config.paths.meicDir, mode === "live" ? "meic_trades.db" : "paper_trades.db");
}

/**
 * The session every MEIC card names when the request names none. Exported for the shared
 * attempts/occupancy readers, which otherwise resolve their own day off one table. Takes the
 * page's era so those cards name the same day the forest and the trade log resolve under an older
 * era, rather than the current era's latest.
 */
export function resolveMeicSession(config: ConsoleConfig, mode: TradingMode, era: string | null = null): string | null {
  const outcome = readOnlyDb<string | null>(meicDbPath(config, mode), (db) => latestMeicSessionIn(db, era));
  if (outcome.status === "ok") return outcome.value;
  if (outcome.status === "absent") return null;
  return UNRESOLVABLE_DAY;
}

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
  const range = { from: q.from ?? null, to: q.to ?? null };
  if (hasRange(range)) {
    const r = rangeClauses("trade_date", range);
    clauses.push(...r.clauses);
    params.push(...r.params);
  }
  // The loop's last session, the one every other card resolves, so no two can name different days.
  const day = hasRange(range) ? null : (q.day ?? latestMeicSessionIn(db, q.era));
  if (day !== null) {
    clauses.push("trade_date = ?");
    params.push(day);
  }
  if (q.outcome === "wins") clauses.push("pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0");
  if (q.outcome === "losses") clauses.push("pnl IS NOT NULL AND pnl - COALESCE(fees, 0) <= 0");
  if (q.outcome === "open") clauses.push("pnl IS NULL");
  clauses.push("COALESCE(status, '') != 'cancelled'");
  if (q.view === "history") clauses.push("pnl IS NOT NULL");
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

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** An `ic_trades` column, or NULL on a ledger that predates it (see flies' `costColumn`). */
function meicCol(db: DatabaseHandle, name: string): string {
  return hasColumn(db, "ic_trades", name) ? name : "NULL";
}

const EMPTY_MEIC_TOTALS: MeicTradeTotals = {
  trades: 0,
  sessions: 0,
  gross: 0,
  fees: 0,
  settlementFees: 0,
  settlementRecorded: 0,
  settlementTrades: 0,
  slippage: 0,
  slippageTrades: 0,
  net: 0,
  feesTrades: 0,
  feesRecorded: 0,
  netRecorded: 0,
  credit: 0,
  byArm: [],
};

/**
 * How a closed iron condor left the book. A side held to the bell and settled in the money makes it
 * `settled`; otherwise a stop or a force-close makes it `closed`; otherwise it `expired` worthless.
 * The exit reason rides beside it for the detail (a stop on one side and an expiry on the other).
 */
export function meicExitKind(
  pnl: number | null,
  exitReason: string | null,
  settlementFees: number | null,
  putSettle: number | null,
  callSettle: number | null,
): MeicTradeRow["exitKind"] {
  if (pnl === null) return null;
  const reason = exitReason ?? "";
  const expired = reason.includes("expire");
  const itm = (settlementFees ?? 0) > 0 || (putSettle ?? 0) > 0 || (callSettle ?? 0) > 0;
  if (expired && itm) return "settled";
  if (reason.includes("stop") || reason.includes("force")) return "closed";
  return "expired";
}

/**
 * The standard's money columns for one iron condor. `pnl` is GROSS in this ledger and `fees` the
 * total (core.ledgers' meic rule), so net = pnl − fees and the settlement part comes out of the
 * total exactly once. Exit = gross − entry, so the row adds up by construction.
 */
export function meicTradeCash(r: Record<string, unknown>): Pick<
  MeicTradeRow,
  "entryCash" | "exitCash" | "exitKind" | "gross" | "fees" | "settlementFees" | "slippage" | "net"
> {
  const mult = num(r["dollar_multiplier"]) ?? 100;
  const qty = num(r["quantity"]) ?? 1;
  const credit = num(r["net_credit"]);
  const gross = num(r["pnl"]);
  const total = num(r["fees"]);
  const settlementFees = num(r["settlement_fees"]);
  const put = num(r["put_credit"]);
  const call = num(r["call_credit"]);
  const entryCash = meicEntryCash(credit, put, call, mult, qty);
  const closed = gross !== null;
  return {
    entryCash,
    exitCash: closed && entryCash !== null ? round2(gross - entryCash) : null,
    exitKind: meicExitKind(gross, str(r["exit_reason"]), settlementFees, num(r["put_settle_value"]), num(r["call_settle_value"])),
    gross,
    fees: total !== null && settlementFees !== null ? round2(total - settlementFees) : total,
    settlementFees: closed ? settlementFees : null,
    slippage: num(r["slippage_dollars"]),
    // A row that never recorded its fees has no known net: n/r, never gross less a $0 fee.
    net: closed && total !== null ? round2(gross - total) : null,
  };
}

/**
 * Half a cent a share: the most a `net_credit` rounded to the cent can sit from its two sides'
 * sum. Beyond it the sides and the net describe different fills.
 */
const SIDE_SUM_TOLERANCE = 0.0051;

/**
 * Entry cash for one IC. From the two sides when they sum to the row's `net_credit`, rounded per
 * side exactly as the write path rounds each side's P&L -- otherwise the rounded `net_credit`
 * leaves a worthless expiry a cent off $0. When they do not, the net credit is the entry: it is the
 * fill the row's gross was booked against. Since 2026-10-12 the live loop
 * (`live_loop.entry_fill_credits`) writes sides that sum to the fill exactly, from the broker's legs
 * or split in proportion; earlier live rows kept the sides modelled at quote time, and the 2026-10-14
 * ledger corrections moved `net_credit` to the confirmed fill without touching them (row 7: sides
 * 0.14 + 0.215, fill 0.43, gross (0.43 − 0.39) × 100 = $4.00).
 */
export function meicEntryCash(credit: number | null, put: number | null, call: number | null, mult: number, qty: number): number | null {
  const sidesAgree = put !== null && call !== null && (credit === null || Math.abs(put + call - credit) <= SIDE_SUM_TOLERANCE);
  if (sidesAgree) return round2(round2(put * mult * qty) + round2(call * mult * qty));
  return credit !== null ? round2(credit * mult * qty) : null;
}

/**
 * The trade log's sortable columns, by the web tables' column ids (`MEIC_TRADE_SORTS` beside
 * `HISTORY_COLUMNS` in pages/Meic/MeicTables.tsx, which a test holds to this list). Both views of
 * the log share them. Each reads the served field its cell renders -- `net` is the derived
 * gross − fees, not the ledger's pnl -- and `date` breaks ties on the entry stamp so a session's
 * condors keep their clock order; `time` is the clock time alone, for ranking entry slots.
 */
export const MEIC_TRADE_SORTS: SortAccessors<MeicTradeRow> = {
  date: (r) => `${r.tradeDate} ${r.entryTime ?? ""}`,
  time: (r) => (r.entryTime !== null && r.entryTime.length >= 16 ? r.entryTime.slice(11, 16) : null),
  sym: (r) => r.symbol,
  put: (r) => r.putStrike,
  call: (r) => r.callStrike,
  wing: (r) => r.wingWidth,
  qty: (r) => r.quantity,
  price: (r) => r.netCredit,
  entry: (r) => r.entryCash,
  exit: (r) => r.exitCash,
  gross: (r) => r.gross,
  fees: (r) => r.fees,
  settle: (r) => r.settlementFees,
  slip: (r) => r.slippage,
  net: (r) => r.net,
};

export function readMeic(config: ConsoleConfig, mode: TradingMode, query: MeicTradeQuery = NO_TRADE_QUERY): MeicPayload {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const trades = withReadOnlyDb<Paged<MeicTradeRow>>(dbPath, emptyPage(query), (db) => {
    const f = tradeFilterSql(db, query);
    return pagedQuery<MeicTradeRow>(
      db,
      {
        columns: `id, trade_date, entry_time, symbol, put_strike, call_strike, wing_width,
                  net_credit, put_credit, call_credit, quantity, status, pnl, fees, exit_reason, iv_rank_at_entry,
                  ${meicCol(db, "dollar_multiplier")} AS dollar_multiplier,
                  ${meicCol(db, "settlement_fees")} AS settlement_fees,
                  ${meicCol(db, "slippage_dollars")} AS slippage_dollars,
                  ${meicCol(db, "put_settle_value")} AS put_settle_value,
                  ${meicCol(db, "call_settle_value")} AS call_settle_value`,
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
        ...meicTradeCash(r),
        exitReason: str(r["exit_reason"]),
        ivRankAtEntry: num(r["iv_rank_at_entry"]),
      }),
      MEIC_TRADE_SORTS,
    );
  });

  const totals = withReadOnlyDb<MeicTradeTotals>(dbPath, EMPTY_MEIC_TOTALS, (db) => {
    const f = tradeFilterSql(db, query);
    const mult = `COALESCE(${meicCol(db, "dollar_multiplier")}, 100) * COALESCE(quantity, 1)`;
    // Settlement is summed over the rows that recorded it, and COUNTed, so a scope holding any row
    // that never recorded it reads n/r rather than a partial sum dressed as the total (101 paper
    // rows, mostly the `book` era, predate the column). The fee total keeps the unrecorded rows'
    // settlement inside `fees`, the standard's rule for a split never recorded. Fees themselves are
    // COUNTed the same way: a row that never recorded its fees (live row 6, 2026-06-29) has no known
    // net, so a scope holding one reads fees and net n/r, with the recorded share beside them.
    const settle = meicCol(db, "settlement_fees");
    const sums = `COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
                  COALESCE(SUM(pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees,
                  COUNT(fees) AS fees_trades,
                  COALESCE(SUM(CASE WHEN fees IS NOT NULL THEN ${settle} END), 0) AS fees_rows_settlement,
                  COALESCE(SUM(CASE WHEN fees IS NOT NULL THEN pnl - fees END), 0) AS net_recorded,
                  COALESCE(SUM(${settle}), 0) AS settlement,
                  COUNT(${settle}) AS settlement_trades,
                  COALESCE(SUM(${meicCol(db, "slippage_dollars")}), 0) AS slippage,
                  COUNT(${meicCol(db, "slippage_dollars")}) AS slippage_trades,
                  COALESCE(SUM(net_credit * ${mult}), 0) AS credit`;
    const shape = (r: Record<string, unknown>) => {
      const trades = Number(r["trades"] ?? 0);
      const gross = Number(r["gross"] ?? 0);
      const fees = Number(r["fees"] ?? 0);
      const feesTrades = Number(r["fees_trades"] ?? 0);
      const feesRecorded = round2(fees - Number(r["fees_rows_settlement"] ?? 0));
      const netRecorded = round2(Number(r["net_recorded"] ?? 0));
      const recorded = round2(Number(r["settlement"] ?? 0));
      const settlementTrades = Number(r["settlement_trades"] ?? 0);
      return {
        trades,
        gross: round2(gross),
        fees: sumIfAllRecorded(feesRecorded, feesTrades, trades),
        settlementFees: sumIfAllRecorded(recorded, settlementTrades, trades),
        settlementRecorded: recorded,
        settlementTrades,
        net: sumIfAllRecorded(netRecorded, feesTrades, trades),
        feesTrades,
        feesRecorded,
        netRecorded,
        credit: round2(Number(r["credit"] ?? 0)),
      };
    };
    const all =
      db
        .prepare<string[], Record<string, unknown>>(`SELECT ${sums} FROM ic_trades WHERE ${f.where} AND pnl IS NOT NULL`)
        .get(...f.params) ?? {};
    const byArm = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${armColumnOf(db, "ic_trades")} AS arm, ${sums} FROM ic_trades
          WHERE ${f.where} AND pnl IS NOT NULL GROUP BY 1`,
      )
      .all(...f.params)
      .map((r) => ({ arm: str(r["arm"]) ?? "—", ...shape(r) }))
      .sort((a, b) => b.netRecorded - a.netRecorded);
    return {
      ...shape(all),
      sessions: Number(all["sessions"] ?? 0),
      slippage: round2(Number(all["slippage"] ?? 0)),
      slippageTrades: Number(all["slippage_trades"] ?? 0),
      byArm,
    };
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

  // What bounds how far the numbers above can be trusted. MEIC records five measurement breaks and
  // none of them reached this page until 2026-08-27 -- including the 2026-08-21 control
  // redefinition, which changes what "control" MEANS either side of that date.
  const integrity = withReadOnlyDb<MeicPayload["integrity"]>(
    dbPath,
    { measurementBreaks: [], schemaDrift: [] },
    (db) => ({
      measurementBreaks: readMeasurementBreaks(db),
      schemaDrift: readSchemaDrift(db, KNOWN_COLUMNS),
    }),
  );

  // The day the log is scoped to, named so the picker can say which day "latest session" is.
  const session = hasRange({ from: query.from ?? null, to: query.to ?? null })
    ? null
    : (query.day ?? resolveMeicSession(config, mode, query.era));
  return { mode, trades, totals, summaries, integrity, session };
}

/** Columns this build knows about, so a ledger written by a NEWER checkout is visible as drift
 *  rather than silently NULLed -- the flies 2026-08-05 failure shape. */
const KNOWN_COLUMNS: Record<string, string[]> = {
  measurement_breaks: ["id", "break_date", "scope", "kind", "reason", "detail", "created_at"],
};

const RESOLVED = "status NOT IN ('cancelled','pending','partial_entry')";


export interface MeicScope {
  symbols: string[];
  /** The arm tags present in the scope's era (stored as `arm`, or `risk_profile` on an older ledger). */
  arms: string[];
  /** Every era present, with its row count, so the page can say what a filter costs. */
  eras: Array<{ era: string; trades: number }>;
  currentEra: string;
}

/**
 * The page-wide scope selects' own options.
 *
 * `symbols` and `arms` are narrowed to the SAME era the data is, because a select that offers
 * more than the scope can return is lying about what is reachable. Measured on the paper ledger:
 * the unfiltered arm list carries 14 names while the current era holds 3 -- the retired ladder
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
  const empty: MeicScope = { symbols: [], arms: [], eras: [], currentEra: CURRENT_ERA };
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
      arms: db
        .prepare<string[], { p: string }>(
          `SELECT DISTINCT ${armColumnOf(db, "ic_trades")} AS p FROM ic_trades
            WHERE ${armColumnOf(db, "ic_trades")} IS NOT NULL${and} ORDER BY p`,
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

/**
 * An IC's capital at risk = (wing width - credit received) x 100 x quantity, applied to still-open
 * positions -- the SAME formula `core.ledgers._meic_closed`'s `_capital()` already uses for CLOSED
 * trades (validated there, feeding calibration/capture-rate metrics), and the same one
 * `analytics.headline`'s new `open_capital_at_risk` field computes in Python. Mirrored here rather
 * than bridged because meic.ts already mirrors `ic_trades` directly for every other read this
 * package does (this file's own docstring: "the largest mirror in this package") -- a subprocess
 * for one aggregate would be a second read strategy for the one module already committed to the
 * first. `meic-mirror.test.ts` checks this against `python run.py headline`'s own field. The
 * multiplier is hardcoded 100 to match every other gross/net query already in this file (line
 * ~910/965) rather than reading `dollar_multiplier`, which nothing else here reads either.
 */
export function readMeicOpenExposure(config: ConsoleConfig, mode: TradingMode): { open: number; capitalAtRisk: number } {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty = { open: 0, capitalAtRisk: 0 };
  return withReadOnlyDb(dbPath, empty, (db) => {
    const r = db
      .prepare<[], { n: number; cap: number | null }>(
        `SELECT COUNT(*) AS n,
                COALESCE(SUM((wing_width - COALESCE(net_credit, 0.0)) * 100 * COALESCE(quantity, 1)), 0.0) AS cap
           FROM ic_trades WHERE status IN ('open', 'partial')`,
      )
      .get();
    if (r === undefined) return empty;
    return { open: r.n, capitalAtRisk: Math.round((r.cap ?? 0) * 100) / 100 };
  });
}

export interface MeicPerformance {
  mode: TradingMode;
  /** Every arm side by side — the variance-test payoff; symbol-scoped, arm-unscoped. */
  arms: Array<{
    arm: string;
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
    undersampledFlag: boolean;
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
    arms: [],
    equity: [],
    risk: { sharpe: null, sortino: null, calmar: null, recoveryFactor: null, sampleSize: 0, undersampledFlag: false, sharpeOverfitFlag: false },
    periods: [],
    studyArms: [],
    bySession: [],
    byIvRank: [],
    regimeCoverage: [],
  };
  return withReadOnlyDb<MeicPerformance>(dbPath, empty, (db) => {
    // `risk_profile` until meic's column moves; an old live ledger has neither, and reads untagged.
    const armColumn = findArmColumn(db, "ic_trades");
    const hasProfile = armColumn !== null;
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
      clauses.push(`${armColumn} = ?`);
      params.push(profile);
    }
    const where = clauses.join(" AND ");

    // --- profile comparison: symbol-scoped, profile-unscoped (compare them all) ---
    //
    // `pnl IS NOT NULL` is load-bearing and is NOT implied by RESOLVED. RESOLVED is a deny-list
    // (`NOT IN ('cancelled','pending','partial_entry')`) and this ledger holds none of those, so it
    // admits every row including the still-open ones; the module's own analytics uses an allow-list
    // (`status IN ('stopped','expired','force_closed')`) and admits only settled trades. On this
    // ledger the two tests coincide exactly — every open row has a NULL pnl and every settled row
    // has one — so this is the same filter the module applies, written in the terms this file uses.
    //
    // Without it an open position contributes its FEES with no gross, reporting each arm down by
    // exactly what it has paid so far: `advised:control` read gross 0, fees 860.83, net -860.83 on
    // 125 rows that have not settled. `readMeicAnalytics` below already guards this by subtracting
    // inside the SUM, and its comment describes this exact failure — "a number that looks like a
    // result". The guard was applied there and not here. Caught by server/test/meic-mirror.test.ts
    // on its first run, which is what that test exists for.
    const profileClauses = [RESOLVED, "pnl IS NOT NULL", `${armColumn ?? "arm"} IS NOT NULL`];
    const profileParams: string[] = [];
    if (eraOn) {
      profileClauses.push("era = ?");
      profileParams.push(activeEra);
    }
    if (symbol !== null) {
      profileClauses.push("symbol = ?");
      profileParams.push(symbol);
    }
    const arms = hasProfile
      ? (() => {
          const rows = db
            .prepare<string[], Record<string, unknown>>(
              `SELECT ${armColumn} AS arm, trade_date, pnl, fees FROM ic_trades
                WHERE ${profileClauses.join(" AND ")} ORDER BY arm, trade_date, entry_time`,
            )
            .all(...profileParams);
          const groups = new Map<string, Array<Record<string, unknown>>>();
          for (const r of rows) {
            const k = String(r["arm"]);
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
                arm: prof,
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

    // --- study arms: one cumulative line per arm (ignores the page filters, but not the era) ---
    const studyArms = hasProfile
      ? (() => {
          const rows = db
            .prepare<string[], Record<string, unknown>>(
              `SELECT ${armColumn} AS arm, trade_date, COALESCE(SUM(pnl - COALESCE(fees, 0)), 0) AS net FROM ic_trades
                WHERE ${RESOLVED} AND ${armColumn} IS NOT NULL${eraOn ? " AND era = ?" : ""}
                GROUP BY arm, trade_date ORDER BY arm, trade_date`,
            )
            .all(...(eraOn ? [activeEra] : []));
          const byArm = new Map<string, Array<{ date: string; cumulative: number }>>();
          const running = new Map<string, number>();
          for (const r of rows) {
            const arm = String(r["arm"]);
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
      arms,
      equity,
      risk,
      periods,
      studyArms,
      bySession: breakdown("COALESCE(session_quality, 'untagged')"),
      byIvRank: breakdown(
        `CASE WHEN iv_rank_at_entry IS NULL THEN 'unknown'
              WHEN iv_rank_at_entry < 0.25 THEN 'under 25/100'
              WHEN iv_rank_at_entry < 0.50 THEN '25-50/100'
              WHEN iv_rank_at_entry < 0.75 THEN '50-75/100'
              ELSE '75/100 and over' END`,
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
   * Today's result per arm — which arm actually made the money, the question the page could
   * not answer. The Performance tab's per-arm table is cumulative and states that it ignores
   * the page's scope, so it never covered this.
   */
  byArm: Array<{ arm: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  /** Today's fee drag per arm, same grouping as byArm: `credit` is premium collected,
   *  `fees` trading fees (settlement out where recorded), `dragPct` = fees ÷ credit (`feeDragPct`). */
  armFeeDrag: Array<{ arm: string; credit: number; fees: number; settlementFees: number | null; net: number; dragPct: number | null }>;
  exitReasons: Array<{ reason: string; count: number }>;
  /** The scope's fee drag over settled rows: premium collected, trading fees, settlement (null =
   *  n/r on any row) and net, so credit is never called gross and fees never carry settlement. */
  feeDrag: { credit: number; fees: number; settlementFees: number | null; netPnl: number; dragPct: number | null };
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
    byArm: [],
    armFeeDrag: [],
    exitReasons: [],
    feeDrag: { credit: 0, fees: 0, settlementFees: 0, netPnl: 0, dragPct: null },
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
    const armRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${armColumnOf(db, "ic_trades")} AS profile, ${SETTLED} AS trades,
                ${NET} AS net, ${WINS} AS wins, ${LOSSES} AS losses,
                COALESCE(SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN pnl - COALESCE(fees, 0) ELSE 0 END), 0) AS won,
                COALESCE(SUM(CASE WHEN pnl - COALESCE(fees, 0) < 0 THEN COALESCE(fees, 0) - pnl ELSE 0 END), 0) AS lost
           FROM ic_trades WHERE ${RESOLVED} AND trade_date = ?${sc.and}
          GROUP BY profile HAVING trades > 0 ORDER BY net DESC`,
      )
      .all(today, ...sc.params);
    const byArm = armRows.map((r) => {
      const trades = Number(r["trades"]);
      const wins = Number(r["wins"]);
      const losses = Number(r["losses"]);
      const lost = Number(r["lost"]);
      const net = Number(r["net"]);
      return {
        arm: String(r["profile"] ?? "?"),
        trades,
        net,
        winPct: wins + losses > 0 ? (wins / (wins + losses)) * 100 : null,
        avg: trades > 0 ? net / trades : null,
        profitFactor: lost > 0 ? Number(r["won"]) / lost : null,
      };
    });

    // Fee drag is trading fees ÷ premium collected (`feeDragPct`, the costs page's definition).
    // `fees` folds settlement in on this ledger, so the recorded settlement comes out; the credit is
    // the entry credit, whole position, with the trade totals' multiplier. Restricted to settled rows
    // so credit, fees and net all describe the same trades -- a drag percentage mixing an unsettled
    // row's fees into a settled row's credit means nothing.
    const settleCol = meicCol(db, "settlement_fees");
    const DRAG_SUMS = `COALESCE(SUM(net_credit * COALESCE(${meicCol(db, "dollar_multiplier")}, 100) * COALESCE(quantity, 1)), 0) AS credit,
                COALESCE(SUM(fees), 0) AS fees_total, COALESCE(SUM(${settleCol}), 0) AS settlement,
                COUNT(${settleCol}) AS settlement_rows, COUNT(*) AS rows_n, ${NET} AS net`;
    const dragOf = (r: Record<string, unknown>) => {
      const credit = round2(Number(r["credit"] ?? 0));
      const recorded = round2(Number(r["settlement"] ?? 0));
      const fees = round2(Number(r["fees_total"] ?? 0) - recorded);
      return {
        credit,
        fees,
        settlementFees: sumIfAllRecorded(recorded, Number(r["settlement_rows"] ?? 0), Number(r["rows_n"] ?? 0)),
        net: Number(r["net"] ?? 0),
        dragPct: feeDragPct(fees, credit),
      };
    };
    const armFeeDrag = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${armColumnOf(db, "ic_trades")} AS profile, ${DRAG_SUMS}
           FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL AND trade_date = ?${sc.and}
          GROUP BY profile ORDER BY profile`,
      )
      .all(today, ...sc.params)
      .map((r) => ({ arm: String(r["profile"] ?? "?"), ...dragOf(r) }));

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

    // Settled rows only, as above: an open row's fees against no result yet was the same mix.
    const fd = dragOf(
      db
        .prepare<string[], Record<string, unknown>>(
          `SELECT ${DRAG_SUMS} FROM ic_trades WHERE ${RESOLVED} AND pnl IS NOT NULL${sc.and}`,
        )
        .get(...sc.params) ?? {},
    );
    return {
      mode,
      periods,
      byArm,
      armFeeDrag,
      exitReasons,
      feeDrag: { credit: fd.credit, fees: fd.fees, settlementFees: fd.settlementFees, netPnl: fd.net, dragPct: fd.dragPct },
    };
  });
}

// ── The MEIC profit forest ──────────────────────────────────────────────────

export interface MeicForestArm {
  arm: string;
  /** One entry per open IC, so a nested condor reads as nested rather than as one lumpy line. */
  positions: Array<{ icOrderId: string; putStrike: number; callStrike: number; wingWidth: number; putWing: number | null; callWing: number | null; netCredit: number; quantity: number }>;
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
  /** The ONE symbol this forest draws (see readMeicForest), and every symbol the day traded. */
  symbol: string | null;
  symbols: string[];
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
  releasedStrikes: Array<{ arm: string; strike: number; right: "P" | "C"; at: string | null }>;
  /** The spot marker (`forestSpot`): the stream cache's last trade on today's session, the settlement
   *  print on a past one, the last entry's spot only when neither is there. */
  lastSpot: number | null;
}

/** A cached spot older than this is not drawn as "now": a stalled streamer looks like a quiet market. */
const FOREST_SPOT_MAX_AGE_S = 15 * 60;

/** Where the forest's spot marker goes. Until 2026-10-15 this was the last ENTRY's spot, so the
 *  marker froze at the day's final entry while the 30-second refetch redrew the same number. Today's
 *  session now reads the shared stream cache (the loops' own source, age-gated); a past session
 *  shows its settlement print; the last entry is the fallback. */
function forestSpot(
  config: ConsoleConfig,
  tradeDate: string,
  symbol: string | null,
  rows: Array<Record<string, unknown>>,
): number | null {
  const nowEt = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const cached = (maxAgeS: number): number | undefined => {
    if (!symbol) return undefined;
    const q = cachedQuote(config, symbol, maxAgeS);
    const mid = q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : undefined;
    return q?.last ?? mid;
  };
  const settled = rows.map((r) => num(r["settle_underlying"])).find((v) => v !== null);
  if (tradeDate === today) {
    // During the session: the fresh cached price.
    const live = cached(FOREST_SPOT_MAX_AGE_S);
    if (live !== undefined) return live;
    // After the close the cache stops moving, so "fresh" fails every evening and the marker fell
    // back to the last ENTRY's spot (2026-10-15: SPX 7752.98 drawn against a 7744.06 close). The
    // day's settlement print once the book settles; else the cache's last price, provided it is
    // from TODAY's session (age bounded by the ET midnight), never yesterday's.
    if (settled !== undefined && settled !== null) return settled;
    const sinceMidnightS = nowEt.getHours() * 3600 + nowEt.getMinutes() * 60 + nowEt.getSeconds();
    const lastToday = cached(sinceMidnightS);
    if (lastToday !== undefined) return lastToday;
  } else if (settled !== undefined && settled !== null) {
    return settled;
  }
  return num(rows[rows.length - 1]?.["underlying_price_entry"]);
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
  // Per-side widths: an `equalize_credit` pair sizes its two wings independently, so the narrower
  // side's long is NOT at the scalar. Absent or NULL (every other row) falls back to the scalar.
  putWing?: number | null;
  callWing?: number | null;
  netCredit: number;
}): Leg[] {
  const putWing = row.putWing ?? row.wingWidth;
  const callWing = row.callWing ?? row.wingWidth;
  return [
    { kind: "put", quantity: -1, price: row.netCredit, strike: row.putStrike },
    { kind: "put", quantity: 1, price: 0, strike: row.putStrike - putWing },
    { kind: "call", quantity: -1, price: 0, strike: row.callStrike },
    { kind: "call", quantity: 1, price: 0, strike: row.callStrike + callWing },
  ];
}

/** The price grid the forest is drawn on: every strike in play plus a margin either side. */
export function forestGrid(strikes: number[]): number[] {
  if (strikes.length === 0) return [];
  const lo = Math.min(...strikes);
  const hi = Math.max(...strikes);
  // Priced at the strikes' own step (the smallest gap between distinct strikes: 5 on SPX, 1 on
  // XSP), so every kink of every condor lands on a grid point. 121 points spread over the whole
  // span drew a 5-wide SPX condor as a spike, and across two symbols the span was thousands wide.
  const distinct = [...new Set(strikes.map((k) => Math.round(k * 100) / 100))].sort((a, b) => a - b);
  let step = Infinity;
  for (let i = 1; i < distinct.length; i++) step = Math.min(step, distinct[i]! - distinct[i - 1]!);
  if (!Number.isFinite(step) || step <= 0) step = Math.max(hi * 0.001, 0.5);
  const pad = Math.max((hi - lo) * 0.35, hi * 0.01);
  const start = Math.floor((lo - pad) / step) * step;
  const end = Math.ceil((hi + pad) / step) * step;
  // A wide book on a fine step: coarsen by whole multiples so the payload stays bounded.
  while ((end - start) / step > 1500) step *= 2;
  const n = Math.floor((end - start) / step) + 1;
  return Array.from({ length: n }, (_, i) => Math.round((start + i * step) * 100) / 100);
}

/**
 * Per-arm expiry-payoff curves for the open book on one day — MEIC's
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
  /** The card's own symbol pick, used only when the page scope names none. */
  forestSymbol: string | null = null,
): MeicForest {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicForest = {
    mode,
    tradeDate: null,
    symbol: null,
    symbols: [],
    tradesToday: 0,
    openPositions: 0,
    asEntered: [],
    arms: [],
    releasedStrikes: [],
    lastSpot: null,
  };

  return withReadOnlyDb<MeicForest>(dbPath, empty, (db) => {
    const { and, params: scopeParams } = scopeSql(db, scope);
    const tradeDate = day ?? latestMeicSessionIn(db, scope.era);
    if (tradeDate === null) return empty;

    const allRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ic_order_id, ${armColumnOf(db, "ic_trades")} AS arm, symbol, put_strike, call_strike, wing_width,
                ${hasColumn(db, "ic_trades", "put_wing") ? "put_wing" : "NULL AS put_wing"},
                ${hasColumn(db, "ic_trades", "call_wing") ? "call_wing" : "NULL AS call_wing"},
                net_credit, quantity, status, underlying_price_entry,
                ${hasColumn(db, "ic_trades", "settle_underlying") ? "settle_underlying" : "NULL AS settle_underlying"},
                pnl, COALESCE(fees, 0) AS fees
           FROM ic_trades
          WHERE trade_date = ?${and}`,
      )
      .all(tradeDate, ...scopeParams);

    // ONE symbol per forest: an expiry payoff is drawn on that symbol's price axis, and SPX (~7700)
    // and XSP (~770) on one axis stretched it from ~600 to ~8400 and priced every XSP condor at SPX
    // prices. The page's symbol if it names one, else the card's own pick, else the symbol with the
    // most condors that day; the day's symbols ride out so the card can offer the switch.
    const counts = new Map<string, number>();
    for (const r of allRows) {
      const s = str(r["symbol"]);
      if (s !== null && str(r["status"]) !== "cancelled") counts.set(s, (counts.get(s) ?? 0) + 1);
    }
    const symbols = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([s]) => s);
    const chosen =
      scope.symbol ?? (forestSymbol !== null && counts.has(forestSymbol) ? forestSymbol : (symbols[0] ?? null));
    const rows = chosen === null ? allRows : allRows.filter((r) => str(r["symbol"]) === chosen);

    // Open positions build the live curves; the whole day builds the as-entered view.
    const open = rows.filter((r) => str(r["status"]) === "open");
    const entered = rows.filter((r) => str(r["status"]) !== "cancelled");
    // One builder, run over whichever row set is being drawn. The grid is derived from the SAME
    // rows as the curves — a grid built from the open book while the curves came from the whole day
    // would clip the as-entered view to whatever happened to still be open.
    const buildArms = (source: Array<Record<string, unknown>>): MeicForestArm[] => {
      if (source.length === 0) return [];
      const byArm = new Map<string, Array<Record<string, unknown>>>();
      for (const r of source) {
        const arm = str(r["arm"]) ?? "?";
        const list = byArm.get(arm) ?? [];
        list.push(r);
        byArm.set(arm, list);
      }
      const prices = forestGrid(
        source.flatMap((r) => {
          const w = num(r["wing_width"]) ?? 0;
          const p = num(r["put_strike"]) ?? 0;
          const c = num(r["call_strike"]) ?? 0;
          return [p - w, p, c, c + w];
        }),
      );
      return [...byArm.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([arm, list]) => {
          const positions = list.map((r) => ({
            icOrderId: str(r["ic_order_id"]) ?? "",
            putStrike: num(r["put_strike"]) ?? 0,
            callStrike: num(r["call_strike"]) ?? 0,
            wingWidth: num(r["wing_width"]) ?? 0,
            // Per-side widths on an `equalize_credit` pair; null on every other row, where the
            // readers fall back to `wingWidth`.
            putWing: num(r["put_wing"]),
            callWing: num(r["call_wing"]),
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
          return { arm, positions, prices, pnl, perPosition, outcome };
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
    const pushReleased = (arm: string, strike: number, right: "P" | "C") => {
      const key = `${arm}|${right}|${strike}`;
      if (releasedKeys.has(key)) return;
      releasedKeys.add(key);
      released.push({ arm, strike, right, at: null });
    };
    for (const r of rows) {
      const status = str(r["status"]);
      if (status === "open") continue;
      const arm = str(r["arm"]) ?? "?";
      const w = num(r["wing_width"]) ?? 0;
      // Per-side widths on an `equalize_credit` pair, else the scalar. A released long sits at the
      // wing the trade was actually built with.
      const putWing = num(r["put_wing"]) ?? w;
      const callWing = num(r["call_wing"]) ?? w;
      const p = num(r["put_strike"]);
      const c = num(r["call_strike"]);
      if (p !== null) {
        pushReleased(arm, p, "P");
        if (putWing) pushReleased(arm, p - putWing, "P");
      }
      if (c !== null) {
        pushReleased(arm, c, "C");
        if (callWing) pushReleased(arm, c + callWing, "C");
      }
    }

    const symbol = chosen ?? str(open[0]?.["symbol"] ?? rows[0]?.["symbol"]) ?? null;
    const lastSpot = forestSpot(config, tradeDate, symbol, rows);
    return {
      mode,
      tradeDate,
      symbol,
      symbols,
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
export function readMeicDivergence(
  config: ConsoleConfig,
  mode: TradingMode,
  day: string | null,
  era: string | null = null,
): MeicDivergence {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicDivergence = { date: null, ticks: 0, allAgreeRatePct: null, pairs: [], outcomes: [] };
  return withReadOnlyDb<MeicDivergence>(dbPath, empty, (db) => {
    if (!hasTable(db, "entry_attempts")) return empty;
    const date = day ?? latestMeicSessionIn(db, era);
    if (date === null) return empty;

    const rows = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT ts, symbol, ${armColumnOf(db, "entry_attempts")} AS arm, outcome FROM entry_attempts
          WHERE trade_date = ? ORDER BY ts`,
      )
      .all(date);

    const ticks = new Map<string, Record<string, string>>();
    const outcomeCounts = new Map<string, number>();
    for (const r of rows) {
      const profile = str(r["arm"]);
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
        .map(([arms, matches]) => ({
          arms,
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


/**
 * MEIC's costs page: each arm's closed condors, judged against the credit received, and its entry
 * outcomes per session. The modelled fill already concedes slippage, so it is shown beside the row
 * and never subtracted again. Scoped to `era` like the trade log ("ALL" pools every era), from the
 * paper or live ledger.
 */
export function readMeicCosts(config: ConsoleConfig, mode: TradingMode, era: string | null = null): ArmCostsPayload {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const empty: ArmCostsPayload = { arms: [], slippageInGross: true, since: null, entryOutcomes: [], entryOutcomesAll: [] };
  return withReadOnlyDb<ArmCostsPayload>(path.join(config.paths.meicDir, file), empty, (db) => {
    const sc = scopeSql(db, { era, symbol: null, profile: null } as MeicScopeFilter);
    const mult = `COALESCE(${meicCol(db, "dollar_multiplier")}, 100) * COALESCE(quantity, 1)`;
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${armColumnOf(db, "ic_trades")} AS arm, pnl AS gross, fees AS fees_total,
                ${meicCol(db, "settlement_fees")} AS settlement_fees,
                ${meicCol(db, "slippage_dollars")} AS slippage_dollars,
                net_credit * ${mult} AS premium, trade_date
           FROM ic_trades WHERE pnl IS NOT NULL AND COALESCE(status, '') != 'cancelled'${sc.and}`,
      )
      .all(...sc.params);
    const since = rows.reduce<string | null>((m, r) => {
      const d = str(r["trade_date"]);
      return d !== null && (m === null || d < m) ? d : m;
    }, null);
    const scoped = (era ?? CURRENT_ERA) !== "ALL";
    return {
      arms: concededArmMoney(rows),
      slippageInGross: true,
      since: scoped ? since : null,
      entryOutcomes: entryOutcomes(db, "entry_attempts", scoped ? since : null),
      entryOutcomesAll: entryOutcomes(db, "entry_attempts", null),
    };
  });
}

// ---- The summary slide's one read -------------------------------------------------------------
//
// One endpoint returning every figure the slide renders, so the slide's cards cannot disagree
// with each other the way per-card fetches would let them (the hero's fees summed over one
// window while the by-arm rows summed over another). The slide derives NOTHING itself beyond
// presentation arithmetic (a percentage of a figure this payload already carries) — every number
// is computed once, here, from the ledger.
//
// Money follows the suite's one layout (packages/console/CLAUDE.md), the same arithmetic
// `readMeic`'s totals block uses (line ~385): `pnl` is GROSS in this ledger and `fees` its
// total, so net = pnl − fees, with the settlement split read out of the total exactly once. Win
// rates are fee-adjusted like every net here. Breakdown buckets are the module's own SQL
// verbatim from `readMeicDeepAnalytics` — the same labels the sessions page renders.

export interface MeicSummaryBreakdown {
  bucket: string;
  trades: number;
  winPct: number | null;
  avgNet: number | null;
}

/** The toolbar's range segments. Exported so the web side and the tests speak one list. */
export const MEIC_SUMMARY_RANGES = ["day", "week", "month", "year", "all"] as const;
export type MeicSummaryRange = (typeof MEIC_SUMMARY_RANGES)[number];

/**
 * The window each range name stands for, ET-anchored the same way `readMeicAnalytics`'s periods
 * are: the module's sessions are ET facts, so a "week" starting on the viewer's Sunday would pool
 * the wrong days. "day" is scoped to the page's RESOLVED session, not wall-clock today — a weekend
 * view of Friday's session must not answer "no trades" — and an unknown range name falls back to
 * it rather than silently widening.
 */
export { summaryWindow };

function toNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function readMeicSummary(
  config: ConsoleConfig,
  mode: TradingMode,
  scope: MeicScopeFilter,
  range: string | undefined,
  day: string | null,
  entryPage: number | undefined,
): MeicSummarySlice {
  const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.meicDir, file);
  const empty: MeicSummarySlice = {
    mode,
    window: { from: null, to: null, label: "" },
    era: scope.era ?? CURRENT_ERA,
    hero: { trades: 0, sessions: 0, gross: 0, fees: 0, settlement: 0, slippage: 0, net: 0, credit: 0, winPct: null, wins: 0, daysWon: 0, daysLost: 0, openTrades: 0, openRisk: 0 },
    capturePct: null,
    captureRawPct: null,
    returnOnRisk: { capitalAtRisk: null, rorPct: null, rorRawPct: null, coverage: null },
    legs: [],
    pairs: { trades: 0, clean: 0, cleanPct: null, doubleStopPct: null, breakevenBarPct: null },
    entryTime: [],
    evaluations: { evaluated: 0, filled: 0, refused: 0, outcomes: [], market: { gexNegPct: null, medianIvRank: null, quality: null } },
    arms: [],
    delta: [],
    wing: [],
    weekday: [],
    entries: { rows: [], total: 0, pages: 0, page: 1 },
  };
  // No day named: resolve the latest session IN THE SCOPE'S ERA, exactly as every other MEIC card
  // does (`readMeic`'s own comment: "today" scoped to nothing answers the wrong day on a no-trade
  // session). The window bounds are computed from the RESOLVED day, so "day" means the session
  // the rest of the page names.
  const resolvedDay = day ?? resolveMeicSession(config, mode, scope.era);
  // One snapshot: the hero and the arm rows are separate queries, and must sum over the same ledger.
  return withReadSnapshot<MeicSummarySlice>(dbPath, empty, (db) => {
    const win = summaryWindow(range, resolvedDay);
    const era = scope.era ?? CURRENT_ERA;
    const eraBound = era !== "ALL";
    // The trade side takes the file's own scope helper (era + symbol + arm, column-guarded), then
    // the window bounds on trade_date — the history table's own convention (`rangeClauses`).
    const sc = scopeSql(db, scope);
    const rc = rangeClauses("trade_date", { from: win.from, to: win.to });
    const winAnd = rc.clauses.length > 0 ? ` AND ${rc.clauses.join(" AND ")}` : "";
    const where = `${RESOLVED}${sc.and}${winAnd}`;
    const params = [...sc.params, ...rc.params];
    const mult = `COALESCE(${meicCol(db, "dollar_multiplier")}, 100) * COALESCE(quantity, 1)`;
    const settleCol = meicCol(db, "settlement_fees");
    const slipCol = meicCol(db, "slippage_dollars");
    const armCol = armColumnOf(db, "ic_trades");
    // Same settled-row denominators the analytics read defines (line ~1096): trades that contribute
    // to a money figure, and the fee-adjusted net they sum to.
    const NET = "COALESCE(SUM(pnl - COALESCE(fees, 0)), 0)";
    const SETTLED = "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END)";

    // -- Hero. The totals shape readMeic computes (line ~385) over the window: gross = SUM(pnl)
    //    over settled rows, fees = total − settlement, net = gross − total. Unsettled rows (NULL
    //    pnl) drop out of every money figure but still count toward trades-attributed fields.
    const t = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
                COALESCE(SUM(pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees_total,
                COALESCE(SUM(${settleCol}), 0) AS settlement,
                SUM(CASE WHEN ${settleCol} IS NULL THEN 1 ELSE 0 END) AS settle_missing,
                COALESCE(SUM(${slipCol}), 0) AS slippage,
                COALESCE(SUM(net_credit * ${mult}), 0) AS credit,
                SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins
           FROM ic_trades WHERE ${where} AND pnl IS NOT NULL`,
      )
      .get(...params) ?? {};
    // The rings' session arc: sessions whose fee-adjusted net was positive or negative. Same
    // settled-row rule — a session of only unsettled rows counts in neither.
    const days = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COALESCE(SUM(pnl - COALESCE(fees, 0)), 0) AS net
           FROM ic_trades WHERE ${where} AND pnl IS NOT NULL GROUP BY trade_date`,
      )
      .all(...params);
    let daysWon = 0;
    let daysLost = 0;
    for (const r of days) {
      if (Number(r["net"] ?? 0) > 0) daysWon += 1;
      else if (Number(r["net"] ?? 0) < 0) daysLost += 1;
    }
    const open = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COUNT(*) AS n,
                COALESCE(SUM((wing_width - COALESCE(net_credit, 0.0)) * ${mult}), 0.0) AS cap
           FROM ic_trades WHERE status IN ('open', 'partial')${sc.and}${winAnd}`,
      )
      .get(...params) ?? {};
    const gross = Math.round(Number(t["gross"] ?? 0) * 100) / 100;
    const feesTotal = Number(t["fees_total"] ?? 0);
    // The UNrounded net sum, kept beside the hero's cent-rounded one: the return-on-risk ratio
    // divides what the review fact set's own division divides — the unrounded sum, of which the
    // displayed net is the rounded key. A cent in the fourth decimal place of a percentage.
    const netRaw = Number(t["gross"] ?? 0) - feesTotal;
    // Settlement is n/r unless EVERY settled row recorded its split (the suite's money rule: a
    // partial sum is never shown as the total, and an unrecorded split stays inside fees).
    const settleMissing = Number(t["settle_missing"] ?? 0);
    const settlement = settleMissing > 0 ? null : Math.round(Number(t["settlement"] ?? 0) * 100) / 100;
    const trades = Number(t["trades"] ?? 0);
    const wins = Number(t["wins"] ?? 0);
    const hero = {
      trades,
      sessions: Number(t["sessions"] ?? 0),
      gross,
      fees: Math.round((feesTotal - (settlement ?? 0)) * 100) / 100,
      settlement,
      slippage: Math.round(Number(t["slippage"] ?? 0) * 100) / 100,
      net: Math.round(netRaw * 100) / 100,
      credit: Math.round(Number(t["credit"] ?? 0) * 100) / 100,
      winPct: trades > 0 ? (wins / trades) * 100 : null,
      wins,
      daysWon,
      daysLost,
      openTrades: Number(open["n"] ?? 0),
      openRisk: Math.round(Number(open["cap"] ?? 0) * 100) / 100,
    };

    // -- Capture: net kept of premium sold, clamped to the gauge's domain.
    const captureRawPct = hero.credit > 0 ? (hero.net / hero.credit) * 100 : null;
    const capturePct = captureRawPct === null ? null : Math.max(-50, Math.min(50, captureRawPct));

    // -- Return on risk: the review fact set's `on_max_risk`, mirrored. The denominator is
    //    core.ledgers._meic_closed._capital's formula per settled row — (wing width − credit-or-0)
    //    × multiplier × quantity, ROUNDED PER ROW as _capital rounds it, a missing credit reading
    //    as zero exactly as `net_credit or 0.0` does there — summed over the same rows the hero's
    //    money sums, so the ratio's two sides are one population. A row that cannot support the
    //    formula drops from the DENOMINATOR, never silently from the ratio; its count rides out
    //    as "coverage" so a partial denominator is visible ("9/10"). The numerator is the
    //    window's UNROUNDED net — the fact set's own division divides the unrounded sum while its
    //    displayed net is the rounded key — pinned to the artifact by
    //    test/meic-summary-reconcile.test.ts. Clamped −100..+100 for the needle — a defined-risk
    //    trade cannot lose more than its capital — with the unclamped value kept for the label.
    const capRow = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT COALESCE(SUM(ROUND((wing_width - COALESCE(net_credit, 0.0)) * ${mult}, 2)), 0.0) AS cap,
                COUNT(*) AS n,
                SUM(CASE WHEN wing_width IS NOT NULL
                          AND (wing_width - COALESCE(net_credit, 0.0)) * ${mult} > 0
                    THEN 1 ELSE 0 END) AS cap_n
           FROM ic_trades WHERE ${where} AND pnl IS NOT NULL`,
      )
      .get(...params) ?? {};
    const capN = Number(capRow["cap_n"] ?? 0);
    const capitalAtRisk = capN > 0 ? Math.round(Number(capRow["cap"] ?? 0) * 100) / 100 : null;
    const rorRawPct =
      capitalAtRisk !== null && capitalAtRisk > 0 ? (netRaw / capitalAtRisk) * 100 : null;
    const returnOnRisk = {
      capitalAtRisk,
      rorPct: rorRawPct === null ? null : Math.max(-100, Math.min(100, rorRawPct)),
      rorRawPct,
      coverage: `${capN}/${trades}`,
    };

    // -- Leg outcomes and the pair identity, one pass over the window's resolved ICs joined to
    //    their legs. The subquery-first shape keeps the two `status` columns unambiguous — the
    //    module's own margin read does the same join the same way (analytics.py's comment).
    const legRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT t.ic_order_id, t.fees, t.net_credit, ${mult} AS mult,
                COALESCE(${meicCol(db, "put_stop_cost")}, 0) AS put_stop_cost,
                COALESCE(${meicCol(db, "call_stop_cost")}, 0) AS call_stop_cost,
                ${meicCol(db, "put_settle_value")} AS put_settle_value,
                ${meicCol(db, "call_settle_value")} AS call_settle_value,
                MAX(CASE WHEN l.side = 'put' THEN l.status END) AS put_status,
                MAX(CASE WHEN l.side = 'call' THEN l.status END) AS call_status
           FROM (SELECT * FROM ic_trades WHERE ${where} AND pnl IS NOT NULL) t
           LEFT JOIN ic_spread_legs l ON l.ic_order_id = t.ic_order_id
          GROUP BY t.ic_order_id`,
      )
      .all(...params);
    // Per-side credit SOLD — the wings' split of the entry cash, the same multiplicands the
    // hero's credit sums. Per-side NET is not computed anywhere: pnl is the condor's.
    const creditSums = db
      .prepare<string[], { put: number; call: number }>(
        `SELECT COALESCE(SUM(put_credit * ${mult}), 0) AS put,
                COALESCE(SUM(call_credit * ${mult}), 0) AS call
           FROM ic_trades WHERE ${where} AND pnl IS NOT NULL`,
      )
      .get(...params) ?? { put: 0, call: 0 };
    const legCount = (side: "put" | "call", status: string): number =>
      legRows.filter((r) => str(r[`${side}_status`]) === status).length;
    // `*_stop_cost` is the side's exit PRICE per share (paper.py records the decision's exit
    // price), so the dollar debit is price × multiplier × quantity, summed in whole-position
    // dollars like every other figure on the slide.
    const stopDebit = (r: Record<string, unknown>, side: "put" | "call"): number =>
      (toNum(r[`${side}_stop_cost`]) ?? 0) * (toNum(r["mult"]) ?? 100);
    const perSideStop = {
      put: legRows.reduce((s, r) => s + stopDebit(r, "put"), 0),
      call: legRows.reduce((s, r) => s + stopDebit(r, "call"), 0),
    };
    const legs = (["put", "call"] as const).map((side) => {
      const expired = legCount(side, "expired");
      const stopped = legCount(side, "stopped");
      const known = legRows.filter((r) => str(r[`${side}_status`]) !== null).length;
      // The third state: a leg still OPEN whose settle value is recorded and non-zero is
      // cash-settled ITM — `meicExitKind`'s own settled test, applied per leg.
      const settled = legRows.filter(
        (r) => str(r[`${side}_status`]) === "open" && Math.abs(toNum(r[`${side}_settle_value`]) ?? 0) > 0,
      ).length;
      return {
        side,
        credit: Math.round((side === "put" ? creditSums.put : creditSums.call) * 100) / 100,
        expired,
        stopped,
        settled,
        other: Math.max(0, known - expired - stopped - settled),
        stopCost: Math.round((perSideStop[side] ?? 0) * 100) / 100,
      };
    });
    {
      const drift = Math.round(hero.credit * 100) - Math.round(legs.reduce((s, l) => s + l.credit, 0) * 100);
      if (drift !== 0 && legs.length > 0) {
        const idx = legs.reduce((best, l, i) => (l.credit > legs[best]!.credit ? i : best), 0);
        const row = legs[idx]!;
        row.credit = Math.round((row.credit + drift / 100) * 100) / 100;
      }
    }
    const withLegs = legRows.filter((r) => str(r["put_status"]) !== null && str(r["call_status"]) !== null);
    const pairCount = withLegs.length;
    const clean = withLegs.filter((r) => str(r["put_status"]) === "expired" && str(r["call_status"]) === "expired").length;
    const doubleStop = withLegs.filter((r) => str(r["put_status"]) === "stopped" && str(r["call_status"]) === "stopped").length;
    const avgPairFee = pairCount > 0 ? withLegs.reduce((s, r) => s + (toNum(r["fees"]) ?? 0), 0) / pairCount : 0;
    const avgPairCredit = pairCount > 0 ? withLegs.reduce((s, r) => s + (toNum(r["net_credit"]) ?? 0) * (toNum(r["mult"]) ?? 10000), 0) / pairCount : 0;
    const pairs = {
      trades: pairCount,
      clean,
      cleanPct: pairCount > 0 ? (clean / pairCount) * 100 : null,
      doubleStopPct: pairCount > 0 ? (doubleStop / pairCount) * 100 : null,
      breakevenBarPct: avgPairCredit > 0 ? (avgPairFee / avgPairCredit) * 100 : null,
    };

    // -- Entry-hour bands: the deep-analytics bucket verbatim, over the window. Fee-adjusted net
    //    per band; unsettled rows contribute nothing to the net but count in the band's trades.
    const hourRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${ENTRY_HOUR} AS hour, COUNT(*) AS n,
                COALESCE(SUM(CASE WHEN pnl IS NOT NULL THEN pnl - COALESCE(fees, 0) ELSE 0 END), 0) AS net,
                SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS settled_n
           FROM ic_trades WHERE ${where} GROUP BY hour ORDER BY hour`,
      )
      .all(...params);
    const entryTime = hourRows
      .filter((r) => r["hour"] !== null)
      .map((r) => {
        const h = Number(r["hour"]);
        const count = Number(r["n"] ?? 0);
        const net = Math.round(Number(r["net"] ?? 0) * 100) / 100;
        const wins = Number(r["wins"] ?? 0);
        // Avg per trade is the bar the card draws — opportunity and edge are different
        // questions, and total net answers only the first. The average never travels without
        // its n; the card says the count beside every bar.
        // Wins and the average are over the bucket's SETTLED trades (pnl NOT NULL), the module's
        // convention: an open condor is not a realised $0, so it must not dilute the average,
        // and an hour with every trade still open reads n/r, not 0%.
        const settledN = Number(r["settled_n"] ?? 0);
        const avg = settledN > 0 ? Math.round((net / settledN) * 100) / 100 : null;
        return {
          hour: String(h),
          label: `${String(h).padStart(2, "0")}:00-${String(h + 1).padStart(2, "0")}:00`,
          offSession: h < 9 || h > 15,
          net,
          count,
          avg,
          winPct: settledN > 0 ? Math.round((wins / settledN) * 1000) / 10 : null,
        };
      });

    // -- The gate's ledger. `entry_attempts` carries arm/symbol/trade_date in its own columns, so
    //    the scope rebuilds here — but the ARM column name still comes from the shared detector,
    //    since older ledgers spell it `risk_profile`. The era column is newer still: an untagged
    //    ledger stays unbounded rather than matching nothing.
    // A ledger with no attempts table (an old or freshly created one) shows an empty gate
    // ledger, not an error that would blank the whole slide through withReadOnlyDb's fallback.
    const hasAttempts = hasTable(db, "entry_attempts");
    const attemptsAll = (sql: string, p: string[]): Array<Record<string, unknown>> =>
      hasAttempts ? db.prepare<string[], Record<string, unknown>>(sql).all(...p) : [];
    const attemptArm = hasAttempts ? (findArmColumn(db, "entry_attempts") ?? "arm") : "arm";
    const attemptHasEra = hasAttempts && hasColumn(db, "entry_attempts", "era");
    // `entry_attempts` has no era column in this ledger generation (checked 2026-10), so the era
    // bound rides the era's DATE SPAN via `eraDateSql` — the same bound `daily_summary` takes. A
    // ledger that adds the column uses it directly.
    const attemptEraSql = attemptHasEra ? (eraBound ? { sql: "era = ?", params: [era] } : { sql: null, params: [] }) : eraDateSql(era, "trade_date");
    // `withArm` false builds the by-arm card's scope: every arm, whatever the header's arm select.
    const buildAttemptScope = (withArm: boolean): { clauses: string[]; params: string[] } => {
      const clauses: string[] = [];
      const ps: string[] = [];
      const add = (clause: string | null, param: string | null): void => {
        if (clause !== null && param !== null) {
          clauses.push(clause);
          ps.push(param);
        }
      };
      add(win.from !== null ? "trade_date >= ?" : null, win.from);
      add(win.to !== null ? "trade_date <= ?" : null, win.to);
      if (withArm) add(scope.profile !== null ? `${attemptArm} = ?` : null, scope.profile);
      add(scope.symbol !== null ? "symbol = ?" : null, scope.symbol);
      if (attemptEraSql.sql !== null) {
        clauses.push(attemptEraSql.sql);
        ps.push(...attemptEraSql.params);
      }
      return { clauses, params: ps };
    };
    const { clauses: attemptClauses, params: attemptParams } = buildAttemptScope(true);
    const attemptWhere = attemptClauses.length > 0 ? attemptClauses.join(" AND ") : "1=1";
    // The dominant-detail subquery repeats the outer WHERE over the same table, aliased a2. Every
    // clause here starts with a bare column name, so the alias prefixes cleanly; the params bind
    // SUBQUERY-FIRST, the order the placeholders appear in the SQL text.
    const attemptWhereA2 = attemptClauses.length > 0 ? attemptClauses.map((c) => `a2.${c}`).join(" AND ") : "1=1";
    const attemptParamsTwice = [...attemptParams, ...attemptParams];
    const outcomeOrder = ["filled", "no_fill", "cadence_blocked", "sign_rule_blocked", "gate_blocked", "window_blocked", "no_candidate"];
    const outcomeRows = attemptsAll(
      `SELECT outcome, COUNT(*) AS n,
              (SELECT a2.block_detail FROM entry_attempts a2
                WHERE a2.outcome = a.outcome AND ${attemptWhereA2}
                GROUP BY a2.block_detail ORDER BY COUNT(*) DESC LIMIT 1) AS detail
         FROM entry_attempts a
        WHERE ${attemptWhere}
        GROUP BY outcome`,
      attemptParamsTwice,
    );
    const evaluated = outcomeRows.reduce((s, r) => s + Number(r["n"] ?? 0), 0);
    const filledN = Number(outcomeRows.find((r) => str(r["outcome"]) === "filled")?.["n"] ?? 0);
    const outcomes = outcomeRows
      .filter((r) => str(r["outcome"]) !== "filled")
      .map((r) => ({ outcome: str(r["outcome"]) ?? "?", n: Number(r["n"] ?? 0), dominantDetail: str(r["detail"]) }))
      .sort((a, b) => {
        const ia = outcomeOrder.indexOf(a.outcome);
        const ib = outcomeOrder.indexOf(b.outcome);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || b.n - a.n;
      });
    // The market sketch aggregates exactly the refused rows the outcomes count — one WHERE, no
    // separate sample that could disagree with the bars above it.
    const refusedWhere = `outcome != 'filled' AND ${attemptWhere}`;
    const gexRow = attemptsAll(
      `SELECT AVG(CASE WHEN gex_net < 0 THEN 1.0 ELSE 0.0 END) AS gex, COUNT(*) AS n
         FROM entry_attempts WHERE ${refusedWhere} AND gex_net IS NOT NULL`,
      attemptParams,
    )[0];
    const ivCount = Number(
      attemptsAll(`SELECT COUNT(*) AS n FROM entry_attempts WHERE ${refusedWhere} AND iv_rank IS NOT NULL`, attemptParams)[0]?.["n"] ?? 0,
    );
    const ivMedian =
      ivCount > 0
        ? toNum(
            attemptsAll(
              `SELECT iv_rank AS iv FROM entry_attempts WHERE ${refusedWhere} AND iv_rank IS NOT NULL
                ORDER BY iv_rank LIMIT 1 OFFSET ${Math.floor(ivCount / 2)}`,
              attemptParams,
            )[0]?.["iv"],
          )
        : null;
    const qualityRow = attemptsAll(
      `SELECT session_quality AS q FROM entry_attempts WHERE ${refusedWhere} AND session_quality IS NOT NULL
        GROUP BY session_quality ORDER BY COUNT(*) DESC LIMIT 1`,
      attemptParams,
    )[0];
    const gexNeg = toNum(gexRow?.["gex"]);
    const evaluations = {
      evaluated,
      filled: filledN,
      refused: evaluated - filledN,
      outcomes,
      market: {
        gexNegPct: gexNeg === null ? null : gexNeg * 100,
        medianIvRank: ivMedian,
        quality: str(qualityRow?.["q"]) ?? null,
      },
    };

    // -- By arm: the hero's money per arm, plus each arm's own slice of the gate's ledger, joined
    //    in the map below (not in SQL — the attempts table has no pnl, the trades table no
    //    outcome; the JOIN key is the arm name both sides already speak).
    // The comparison card is every arm by design, so its scope drops the header's arm select
    // (symbol and era still bind). Rows are arm x symbol, so the gate counts join on both.
    const allArms = scopeSql(db, { ...scope, profile: null });
    const armWhere = `${RESOLVED}${allArms.and}${winAnd}`;
    const armParams = [...allArms.params, ...rc.params];
    const armMoney = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${armCol} AS arm, symbol,
                ${SETTLED} AS trades, ${NET} AS net,
                COALESCE(SUM(pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees_total
           FROM ic_trades WHERE ${armWhere} GROUP BY ${armCol}, symbol`,
      )
      .all(...armParams);
    const armAttempt = buildAttemptScope(false);
    const armAttemptWhere = armAttempt.clauses.length > 0 ? armAttempt.clauses.join(" AND ") : "1=1";
    const attemptPerArm = attemptsAll(
      `SELECT ${attemptArm} AS arm, symbol, COUNT(*) AS evaluated,
              SUM(CASE WHEN outcome = 'filled' THEN 1 ELSE 0 END) AS filled
         FROM entry_attempts WHERE ${armAttemptWhere} GROUP BY ${attemptArm}, symbol`,
      armAttempt.params,
    );
    const dominantPerArm = attemptsAll(
      `SELECT ${attemptArm} AS arm, symbol, outcome, COUNT(*) AS n FROM entry_attempts
        WHERE ${armAttemptWhere} AND outcome != 'filled' GROUP BY ${attemptArm}, symbol, outcome`,
      armAttempt.params,
    );
    // Rounding residual: each arm's net is round2 of ITS OWN exact value, so the arms' rounded
    // sums can drift a cent from the hero's rounded total. The suite's money rule says rows add
    // up, so the residual — display rounding, never a money error — is assigned to the
    // largest-magnitude arm, the way a ledger foots a column.
    const plugResidual = (rows: Array<{ net: number }>, total: number): void => {
      const drift = Math.round(total * 100) - Math.round(rows.reduce((s, r) => s + r.net, 0) * 100);
      if (drift !== 0 && rows.length > 0) {
        const idx = rows.reduce((best, r, i) => (Math.abs(r.net) > Math.abs(rows[best]!.net) ? i : best), 0);
        const row = rows[idx]!;
        row.net = Math.round((row.net + drift / 100) * 100) / 100;
      }
    };
    const arms = armMoney
      .map((r) => {
        const arm = str(r["arm"]) ?? "—";
        const symbol = str(r["symbol"]);
        const same = (x: Record<string, unknown>) => str(x["arm"]) === arm && str(x["symbol"]) === symbol;
        const ev = attemptPerArm.find(same);
        const dom = dominantPerArm.filter(same).sort((a, b) => Number(b["n"]) - Number(a["n"]))[0];
        return {
          arm,
          symbol: symbol ?? "—",
          evaluated: Number(ev?.["evaluated"] ?? 0),
          filled: Number(ev?.["filled"] ?? 0),
          refused: ev === undefined ? 0 : Number(ev["evaluated"]) - Number(ev["filled"]),
          dominant: dom === undefined ? null : (str(dom["outcome"]) ?? null),
          net: Math.round((Number(r["gross"] ?? 0) - Number(r["fees_total"] ?? 0)) * 100) / 100,
        };
      });
    // Footed to the card's OWN total (every arm), not the hero's, which the arm select narrows.
    const armsTotal = armMoney.reduce((sum, r) => sum + Number(r["gross"] ?? 0) - Number(r["fees_total"] ?? 0), 0);
    plugResidual(arms, Math.round(armsTotal * 100) / 100);
    arms.sort((a, b) => b.net - a.net);

    // -- Signal breakdowns: the module's buckets verbatim, over the window. Same denominators as
    //    the deep-analytics read: resolved, settled rows; avg net re-derived per bucket, never an
    //    average of averages.
    const signalWhere = `${RESOLVED} AND pnl IS NOT NULL${sc.and}${winAnd}`;
    const breakdown = (bucketExpr: string): MeicSummaryBreakdown[] =>
      db
        .prepare<string[], Record<string, unknown>>(
          `SELECT ${bucketExpr} AS bucket, COUNT(*) AS trades,
                  SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                  AVG(pnl - COALESCE(fees, 0)) AS avg_net
             FROM ic_trades WHERE ${signalWhere} GROUP BY bucket ORDER BY bucket`,
        )
        .all(...params)
        .map((r) => ({
          bucket: String(r["bucket"] ?? "?"),
          trades: Number(r["trades"] ?? 0),
          winPct: Number(r["trades"] ?? 0) > 0 ? (Number(r["wins"] ?? 0) / Number(r["trades"] ?? 1)) * 100 : null,
          avgNet: r["avg_net"] === null ? null : Math.round(Number(r["avg_net"]) * 100) / 100,
        }));
    const delta = breakdown(
      `CASE WHEN ABS(COALESCE(call_delta_at_entry, 0)) < 0.10 THEN '<0.10'
            WHEN ABS(call_delta_at_entry) < 0.15 THEN '0.10-0.15'
            WHEN ABS(call_delta_at_entry) < 0.20 THEN '0.15-0.20'
            ELSE '>=0.20' END`,
    );
    const wing = breakdown(`CAST(CAST(wing_width AS INTEGER) AS TEXT) || '-wide'`);
    // Weekday in CALENDAR order, Mon-Fri, every day present even at zero trades — the label's
    // alphabetical order is not the week's order, and a day that didn't trade is part of the
    // answer (0 trades is a fact of the week, not a missing bucket).
    const weekdayRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT CASE CAST(strftime('%w', trade_date) AS INTEGER)
               WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue' WHEN 3 THEN 'Wed'
               WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri' END AS bucket,
               COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions,
               SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins,
               AVG(pnl - COALESCE(fees, 0)) AS avg_net
          FROM ic_trades WHERE ${where} AND pnl IS NOT NULL
         GROUP BY bucket`,
      )
      .all(...params);
    const byDay = new Map(weekdayRows.map((r) => [String(r["bucket"] ?? "?"), r]));
    const weekday: MeicBreakdownRow[] = ["Mon", "Tue", "Wed", "Thu", "Fri"].map((day) => {
      const r = byDay.get(day);
      if (!r) return { bucket: day, trades: 0, sessions: 0, winPct: null, avgNet: null };
      const trades = Number(r["trades"]);
      return {
        bucket: day,
        trades,
        sessions: Number(r["sessions"]),
        winPct: trades > 0 ? (Number(r["wins"]) / trades) * 100 : null,
        avgNet: r["avg_net"] === null ? null : Number(r["avg_net"]),
      };
    });

    // -- Entries: the window's settled rows, newest first, ONE PAGE of 40 (the card says "page k
    //    of n", the URL carries ?page=, and the count is the same settled rows the money figures
    //    count — the slice's arithmetic stays closed over what it shows). Structure text is the
    //    leg pair in the module's vocabulary; provisional = unsettled (mark, not settlement — the
    //    honesty badge).
    const ENTRY_PAGE_SIZE = 40;
    const page = Math.max(1, Math.floor(entryPage ?? 1));
    const entryTotal = Number(
      (
        db
          .prepare<string[], Record<string, unknown>>(
            `SELECT COUNT(*) AS n FROM ic_trades WHERE ${where} AND pnl IS NOT NULL`,
          )
          .get(...params) ?? {}
      )["n"] ?? 0,
    );
    const entryPages = Math.max(1, Math.ceil(entryTotal / ENTRY_PAGE_SIZE));
    const entryPageClamped = Math.min(page, entryPages);
    const entryRows = db
      .prepare<[...string[], number, number], Record<string, unknown>>(
        `SELECT entry_time, ${armCol} AS arm, symbol, net_credit, ${mult} AS mult,
                status, pnl, fees, ${settleCol} AS settlement,
                ${meicCol(db, "put_settle_value")} AS put_settle_value,
                ${meicCol(db, "call_settle_value")} AS call_settle_value,
                exit_reason
           FROM ic_trades WHERE ${where} AND pnl IS NOT NULL ORDER BY id DESC
          LIMIT ? OFFSET ?`,
      )
      .all(...params, ENTRY_PAGE_SIZE, (entryPageClamped - 1) * ENTRY_PAGE_SIZE);
    const entries = {
      rows: entryRows.map((r) => {
        const credit = (toNum(r["net_credit"]) ?? 0) * (toNum(r["mult"]) ?? 10000);
        const gross = toNum(r["pnl"]);
        const feeTotal = toNum(r["fees"]) ?? 0;
        const setFees = toNum(r["settlement"]) ?? 0;
        const net = gross === null ? null : Math.round((gross - feeTotal) * 100) / 100;
        const exitKind = meicExitKind(gross, str(r["exit_reason"]), setFees, toNum(r["put_settle_value"]), toNum(r["call_settle_value"]));
        return {
          time: str(r["entry_time"]) === null ? null : (str(r["entry_time"]) ?? "").slice(11, 16),
          arm: str(r["arm"]) ?? "—",
          symbol: str(r["symbol"]) ?? "—",
          credit: Math.round(credit * 100) / 100,
          structure:
            exitKind === "settled"
              ? "settled"
              : exitKind === "closed"
                ? "stopped/closed"
                : exitKind === "expired"
                  ? "expired OTM"
                  : str(r["status"]) ?? "open",
          net: net ?? 0,
          provisional: exitKind === null,
        };
      }),
      total: entryTotal,
      pages: entryPages,
      page: entryPageClamped,
    };

    return {
      mode,
      window: win,
      era: eraBound ? era : null,
      hero,
      capturePct,
      captureRawPct,
      returnOnRisk,
      legs,
      pairs,
      entryTime,
      evaluations,
      arms,
      delta,
      wing,
      weekday,
      entries,
    };
  });
}
