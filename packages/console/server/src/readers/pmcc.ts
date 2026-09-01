import fs from "node:fs";
import path from "node:path";
import type {
  Paged,
  PmccAssignment,
  PmccBookCell,
  PmccCycleRow,
  PmccIntegrity,
  PmccMeta,
  PmccOpenPosition,
  PmccPayload,
  PmccRoll,
  PmccShortLeg,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasColumn, num, obj, readJson, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { unrealisedByPosition, NO_UNREALISED } from "./unrealised.js";
import { emptyPage, pagedQuery, FIRST_PAGE, type PageRequest } from "./paging.js";

/**
 * PMCC-99's read layer.
 *
 * Paper only, and that is structural rather than a default: the module has no live loop and no live
 * DB, so there is one store to read and no `mode` anywhere in this file. Its `live.enabled` is a
 * documented placeholder (packages/pmcc/CLAUDE.md).
 *
 * Every query here mirrors `packages/pmcc/src/cherrypick/pmcc/analytics.py`, which is the module's
 * stated ONE query layer. That layer is Python and this one is TypeScript, so the two cannot share
 * code — the mirroring is therefore a discipline, and each function below names the analytics
 * function it answers for. The rule that layer keeps and this one must keep too: `None` never means
 * zero. A position with no usable mark reports a null time value, not $0.00, because "not recorded"
 * and "was zero" are different facts and only one of them means the trade is nearly over.
 *
 * The module's honesty rules (its CLAUDE.md) decide what this file is obliged to surface, not just
 * what it may: early assignment is measured and never modelled, so the exposure figures ride beside
 * every net; a lapsed dividend calendar halts entries loudly; a refused mark is a row, never a gap.
 */

const DB_FILE = "paper_trades.db";

/**
 * The era the module counts as evidence. Its own `headline()` narrows to this by default, so
 * anything tagged to an earlier era is pre-redesign data — mixing it in silently pools two
 * incomparable strategies into one number. Duplicated as a literal here rather than imported,
 * because this package cannot import Python: it must be hand-kept equal to `CURRENT_ERA` in
 * `packages/pmcc/src/cherrypick/pmcc/analytics.py`.
 *
 * One era so far: `"redesign"` (2026-08-23 ->), opened by the single-symbol/single-book redesign
 * and the XSP addition. `era` is an ADDED column — every pre-redesign row reads back `NULL`, which
 * never equals the literal era string, so old rows are excluded by construction. `hasColumn` guards
 * a ledger this build's migration hasn't reached yet (stale checkout), in which case every row is
 * read unscoped rather than the query failing on a missing column.
 */
const CURRENT_ERA = "redesign";

/** Config keys the page renders against. Thresholds the module runs on, not display preferences. */
interface PmccParams {
  tvCloseThreshold: number | null;
  tvManagedExit: boolean | null;
  assignmentExposureTv: number | null;
  longDeltaMin: number | null;
  longDeltaMax: number | null;
  symbols: string[];
  dividends: Record<string, { declaredThrough: string | null; exDates: string[] }>;
  /** Per-symbol settlement style ("physical" | "cash"), read straight through from config's own
   *  `settlement_style` map — the same field `paper_loop.py`/`management.py` gate on. Missing for a
   *  symbol means the module's own config doesn't declare one for it either. */
  settlementStyle: Record<string, string>;
}

/**
 * Columns this console build knows, per migrated table — the TypeScript half of the module's
 * `db.stale_writer_columns` guard.
 *
 * That guard compares the RUNNING CODE to the DATABASE FILE, which is the only comparison that
 * catches a stale checkout writing NULLs all week (the flies 2026-08-05 failure). This package
 * cannot import the Python writer's declaration, so the snapshot is transcribed here and the check
 * runs in the same direction: columns the ledger HAS that this page does not know mean the writer
 * has moved on and these queries may be reading a narrower story than the module is recording.
 *
 * REFRESH THIS when `db.py`'s `_SCHEMA`/`_ADDED_COLUMNS` gain a column. Being out of date is the
 * condition it reports rather than a bug in it — it goes amber, it does not go wrong.
 */
const KNOWN_COLUMNS: Record<string, string[]> = {
  pmcc_positions: [
    "id", "position_id", "symbol", "book", "entry_session", "quantity", "long_expiration", "long_strike",
    "short_expiration", "short_strike", "entry_time", "entry_spot", "long_entry_mid", "short_entry_mid",
    "net_debit", "entry_cost", "entry_slippage", "entry_short_dte", "entry_long_dte", "entry_total_premium",
    "entry_short_intrinsic", "entry_short_tv", "entry_net_tv", "entry_long_extrinsic", "entry_profit_pct",
    "entry_weekly_yield_pct", "entry_downside_protection_pct", "entry_breakeven", "entry_buffer_to_breakeven_pct",
    "entry_long_delta", "entry_short_delta", "entry_long_iv", "entry_short_iv", "long_selected_by",
    "keltner_mid", "keltner_atr", "keltner_days", "keltner_distance_atr", "keltner_bounce_atr",
    "keltner_prev_close_gap", "advice_params", "roll_count", "exposure_ticks", "status", "exit_reason",
    "closed_at", "closed_session", "exit_value", "exit_cost", "exit_slippage", "settlement_spot",
    "itm_settlements", "gross_pnl", "fees", "created_at", "updated_at", "era",
  ],
  pmcc_legs: [
    "id", "position_id", "leg_role", "occ_symbol", "streamer_symbol", "expiration", "strike", "option_type",
    "action", "quantity", "entry_bid", "entry_ask", "entry_mid", "entry_iv", "entry_delta", "status",
    "close_kind", "closed_at", "close_bid", "close_ask", "close_value", "created_at", "updated_at",
  ],
  pmcc_marks: [
    "id", "position_id", "leg_role", "marked_at", "session_date", "bid", "ask", "mid", "delta", "iv", "vega",
    "spot", "short_tv", "assignment_exposed", "quote_age_s", "usable", "refusal",
  ],
  pmcc_assignments: [
    "id", "position_id", "leg_role", "symbol", "assigned_session", "assigned_at", "direction", "shares",
    "basis", "strike", "option_type", "status", "disposed_session", "disposed_at", "disposal_price",
    "share_pnl", "fees", "created_at", "updated_at",
  ],
};

/** Days before a declared dividend calendar lapses that the page starts asking for a refresh. */
const DIVIDEND_WARN_DAYS = 14;

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.pmccDir, DB_FILE);
}

/**
 * The module's declared knobs, resolved the way the module itself resolves them: deployed config
 * first, then the repo's, then the shipped example. Missing config is not fatal — the thresholds
 * degrade to null and the cards that need them say so, rather than the page failing whole.
 */
function loadParams(config: ConsoleConfig): PmccParams {
  let doc: Record<string, unknown> | null = null;
  for (const candidate of config.paths.pmccConfigCandidates) {
    doc = readJson(candidate);
    if (doc !== null) break;
  }
  const defaults = obj(doc?.["defaults"]);
  const dividendsBlock = obj(doc?.["dividends"]);
  const dividends: PmccParams["dividends"] = {};
  for (const [symbol, block] of Object.entries(dividendsBlock)) {
    if (symbol.startsWith("_")) continue;
    const b = obj(block);
    dividends[symbol] = {
      declaredThrough: str(b["declared_through"]),
      exDates: Array.isArray(b["ex_dates"]) ? b["ex_dates"].filter((d): d is string => typeof d === "string") : [],
    };
  }
  const symbols = Array.isArray(doc?.["symbols"])
    ? doc["symbols"].filter((s): s is string => typeof s === "string")
    : [];
  const settlementStyleBlock = obj(doc?.["settlement_style"]);
  const settlementStyle: Record<string, string> = {};
  for (const [symbol, style] of Object.entries(settlementStyleBlock)) {
    if (symbol.startsWith("_")) continue;
    const s = str(style);
    if (s !== null) settlementStyle[symbol] = s;
  }
  // `tv_managed_exit` is a bool/0-1 in config the way the module writes it; config-level defaults
  // stay off — the only place it runs true is a frozen `advised:control` row's `advice_params`
  // overlay, read per-position rather than here.
  const tvManagedExitRaw = defaults["tv_managed_exit"];
  const tvManagedExit =
    typeof tvManagedExitRaw === "boolean"
      ? tvManagedExitRaw
      : typeof tvManagedExitRaw === "number"
        ? tvManagedExitRaw !== 0
        : null;
  return {
    tvCloseThreshold: num(defaults["tv_close_threshold"]),
    tvManagedExit,
    assignmentExposureTv: num(defaults["assignment_exposure_tv"]),
    // 2026-08-23 redesign: the long is chosen inside a delta BAND, not past a floor.
    longDeltaMin: num(defaults["long_delta_min"]),
    longDeltaMax: num(defaults["long_delta_max"]),
    symbols,
    dividends,
    settlementStyle,
  };
}

/**
 * The session every card on the page names.
 *
 * Resolved ONCE and unscoped, the way flies learned to: a per-book or per-symbol "latest" lets one
 * card answer for Monday while the card beside it answers for Friday, both correctly labelled and
 * irreconcilable. The loop's own iterations are the primary source because the loop ticks on days
 * that take no position at all — falling back to entries would name the last day something HAPPENED
 * rather than the last day the module RAN, and those differ exactly when the page matters most.
 */
function latestSession(db: DatabaseHandle): string | null {
  const fromLoop = db
    .prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM pmcc_loop_iterations")
    .get()?.d;
  if (fromLoop != null) return fromLoop;
  return db.prepare<[], { d: string | null }>("SELECT MAX(entry_session) AS d FROM pmcc_positions").get()?.d ?? null;
}

/** Mirrors `analytics.exposure()`: exposed and marked tick counts per position. */
function exposureByPosition(db: DatabaseHandle): Map<string, { exposed: number; marked: number }> {
  const out = new Map<string, { exposed: number; marked: number }>();
  const rows = db
    .prepare<[], Record<string, unknown>>(
      `SELECT position_id,
              SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed,
              COUNT(*) AS marked
         FROM pmcc_marks
        WHERE short_tv IS NOT NULL AND usable = 1
        GROUP BY position_id`,
    )
    .all();
  for (const r of rows) {
    out.set(str(r["position_id"]) ?? "", { exposed: Number(r["exposed"] ?? 0), marked: Number(r["marked"] ?? 0) });
  }
  return out;
}

/**
 * The widest leg spread each position was ENTERED at, per position id.
 *
 * Not in `analytics.py` — this is the console asking a question that layer does not, and it asks it
 * off the legs' own recorded entry quotes rather than deriving anything new. The motive is the first
 * session: a leg quoted 17.55/21.10 was crossed to harvest $0.36 of time value, and no figure on
 * the page said so. Widest rather than average, because the round trip pays every leg.
 */
function entrySpreadByPosition(db: DatabaseHandle, ids: string[] | null): Map<string, { pct: number; abs: number }> {
  const out = new Map<string, { pct: number; abs: number }>();
  const sql =
    "SELECT position_id, entry_bid, entry_ask, entry_mid FROM pmcc_legs" +
    (ids === null ? "" : ` WHERE position_id IN (${ids.map(() => "?").join(", ")})`);
  const rows = ids === null
    ? db.prepare<[], Record<string, unknown>>(sql).all()
    : db.prepare<string[], Record<string, unknown>>(sql).all(...ids);
  for (const r of rows) {
    const bid = num(r["entry_bid"]);
    const ask = num(r["entry_ask"]);
    const mid = num(r["entry_mid"]);
    if (bid === null || ask === null || mid === null || mid <= 0) continue;
    const abs = ask - bid;
    const pct = abs / mid;
    const id = str(r["position_id"]) ?? "";
    const prev = out.get(id);
    if (prev === undefined || pct > prev.pct) out.set(id, { pct, abs });
  }
  return out;
}

/** Mirrors `analytics.worksheet()` — open positions plus their latest USABLE short-leg mark. */
function readOpenPositions(db: DatabaseHandle): PmccOpenPosition[] {
  const exposure = exposureByPosition(db);
  const spreads = entrySpreadByPosition(db, null);
  const pnl = unrealisedByPosition(db, {
    positionsTable: "pmcc_positions",
    legsTable: "pmcc_legs",
    marksTable: "pmcc_marks",
  });
  const latestMark = db.prepare<[string], Record<string, unknown>>(
    `SELECT short_tv, spot, marked_at FROM pmcc_marks
      WHERE position_id = ? AND short_tv IS NOT NULL AND usable = 1
      ORDER BY marked_at DESC LIMIT 1`,
  );
  return db
    .prepare<[], Record<string, unknown>>(
      "SELECT * FROM pmcc_positions WHERE status != 'closed' ORDER BY symbol, book",
    )
    .all()
    .map((p) => {
      const positionId = str(p["position_id"]) ?? "";
      const mark = latestMark.get(positionId);
      const exp = exposure.get(positionId);
      return {
        positionId,
        symbol: str(p["symbol"]) ?? "",
        book: str(p["book"]) ?? "",
        status: str(p["status"]) ?? "",
        longStrike: num(p["long_strike"]),
        longExpiration: str(p["long_expiration"]),
        shortStrike: num(p["short_strike"]),
        shortExpiration: str(p["short_expiration"]),
        entrySpot: num(p["entry_spot"]),
        netDebit: num(p["net_debit"]),
        entryNetTv: num(p["entry_net_tv"]),
        entryWeeklyYieldPct: num(p["entry_weekly_yield_pct"]),
        downsideProtectionPct: num(p["entry_downside_protection_pct"]),
        breakeven: num(p["entry_breakeven"]),
        rollCount: num(p["roll_count"]),
        // No usable mark yet is a real state (pre-open, or a refused feed). Null, never zero.
        currentShortTv: mark === undefined ? null : num(mark["short_tv"]),
        currentSpot: mark === undefined ? null : num(mark["spot"]),
        lastMarkAt: mark === undefined ? null : num(mark["marked_at"]),
        exposedTicks: exp?.exposed ?? 0,
        markedTicks: exp?.marked ?? 0,
        entryMaxSpreadPct: spreads.get(positionId)?.pct ?? null,
        entrySession: str(p["entry_session"]) ?? "",
        ...(pnl.get(positionId) ?? NO_UNREALISED),
        entryMaxSpreadAbs: spreads.get(positionId)?.abs ?? null,
      };
    });
}

/**
 * Mirrors `analytics.headline()`: per-book, per-symbol results over CLOSED positions, scoped to
 * `CURRENT_ERA` by default — `era="ALL"` pools every era for an explicit cross-era read.
 *
 * Net is `SUM(gross) - SUM(fees)` — the same single subtraction `cherrypick.core.ledgers` performs
 * for the `pmcc_99` schema. One convention, stated in one place, computed identically here.
 */
function readBooks(db: DatabaseHandle, era: string = CURRENT_ERA): PmccBookCell[] {
  const scoped = era !== "ALL" && hasColumn(db, "pmcc_positions", "era");
  const sql = `SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees,
              SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins,
              SUM(roll_count) AS rolls
         FROM pmcc_positions WHERE status = 'closed'${scoped ? " AND era = ?" : ""}
        GROUP BY book, symbol ORDER BY book, symbol`;
  const rows = scoped
    ? db.prepare<[string], Record<string, unknown>>(sql).all(era)
    : db.prepare<[], Record<string, unknown>>(sql).all();
  return rows.map((r) => {
    const n = Number(r["n"] ?? 0);
    const wins = num(r["wins"]);
    return {
      book: str(r["book"]) ?? "",
      symbol: str(r["symbol"]) ?? "",
      positions: n,
      grossPnl: num(r["gross"]),
      fees: num(r["fees"]),
      netPnl: num(r["net"]),
      winRate: n > 0 && wins !== null ? wins / n : null,
      rolls: num(r["rolls"]),
    };
  });
}

/** Columns the ledger has that this build does not know — see KNOWN_COLUMNS. */
function schemaDrift(db: DatabaseHandle): string[] {
  const drift: string[] = [];
  for (const [table, known] of Object.entries(KNOWN_COLUMNS)) {
    const knownSet = new Set(known);
    let present: Array<Record<string, unknown>>;
    try {
      present = db.prepare<[], Record<string, unknown>>(`PRAGMA table_info(${table})`).all();
    } catch {
      continue;
    }
    for (const col of present) {
      const name = str(col["name"]);
      if (name !== null && !knownSet.has(name)) drift.push(`${table}.${name}`);
    }
  }
  return drift.sort();
}

/** Whether a declared calendar has lapsed, or lapses inside the warning window. */
function dividendRefreshDue(declaredThrough: string | null, session: string | null): boolean {
  // No declared date at all is the loudest case there is: the module refuses entries outright.
  if (declaredThrough === null) return true;
  if (session === null) return false;
  const through = Date.parse(`${declaredThrough}T00:00:00Z`);
  const on = Date.parse(`${session}T00:00:00Z`);
  if (Number.isNaN(through) || Number.isNaN(on)) return false;
  return through - on < DIVIDEND_WARN_DAYS * 86_400_000;
}

/** Mirrors `analytics.mark_coverage()` for one session. */
function readMarkCoverage(db: DatabaseHandle, session: string | null): PmccIntegrity["markCoverage"] {
  if (session === null) return { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] };
  const totals = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM pmcc_marks WHERE session_date = ?",
    )
    .get(session);
  const marks = Number(totals?.["total"] ?? 0);
  const refused = Number(totals?.["refused"] ?? 0);
  const refusals = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT refusal, COUNT(*) AS n FROM pmcc_marks
        WHERE session_date = ? AND usable = 0 AND refusal IS NOT NULL
        GROUP BY refusal ORDER BY n DESC`,
    )
    .all(session)
    .map((r) => ({ reason: str(r["refusal"]) ?? "", n: Number(r["n"] ?? 0) }));
  return { session, marks, refused, refusalShare: marks > 0 ? refused / marks : null, refusals };
}

export function readPmcc(config: ConsoleConfig): PmccPayload {
  const params = loadParams(config);
  const empty: PmccPayload = {
    session: null,
    dbPresent: false,
    openPositions: [],
    openCount: 0,
    books: [],
    integrity: {
      exposure: { positionsWithExposure: 0, exposedTicks: 0, markedTicks: 0 },
      dividends: [],
      markCoverage: { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] },
      schemaDrift: [],
      measurementBreaks: [],
    },
    today: { attempts: [], events: [], lastIteration: null },
    params: {
      tvCloseThreshold: params.tvCloseThreshold,
      tvManagedExit: params.tvManagedExit,
      assignmentExposureTv: params.assignmentExposureTv,
      longDeltaMin: params.longDeltaMin,
      longDeltaMax: params.longDeltaMax,
      symbols: params.symbols,
      settlementStyle: params.settlementStyle,
    },
  };

  return withReadOnlyDb<PmccPayload>(dbPath(config), empty, (db) => {
    const session = latestSession(db);
    const openPositions = readOpenPositions(db);

    // Symbols the page speaks for: the config's declared list, plus anything the ledger holds that
    // config has since dropped. A position whose symbol was removed from config still exists.
    const ledgerSymbols = db
      .prepare<[], Record<string, unknown>>("SELECT DISTINCT symbol FROM pmcc_positions ORDER BY symbol")
      .all()
      .map((r) => str(r["symbol"]) ?? "")
      .filter((s) => s !== "");
    const symbols = [...new Set([...params.symbols, ...ledgerSymbols])];

    const exposureRows = [...exposureByPosition(db).values()];

    const attempts = session === null
      ? []
      : db
          .prepare<[string], Record<string, unknown>>(
            `SELECT symbol, book, outcome, COUNT(*) AS n,
                    MAX(block_detail) AS block_detail, MAX(best_yield) AS best_yield
               FROM pmcc_entry_attempts WHERE trade_date = ?
              GROUP BY symbol, book, outcome ORDER BY symbol, book, n DESC`,
          )
          .all(session)
          .map((r) => ({
            symbol: str(r["symbol"]) ?? "",
            book: str(r["book"]) ?? "",
            outcome: str(r["outcome"]) ?? "",
            n: Number(r["n"] ?? 0),
            blockDetail: str(r["block_detail"]),
            bestYield: num(r["best_yield"]),
          }));

    // pmcc_management_events carries no symbol of its own -- it is keyed on position_id, so the
    // symbol comes from a join to pmcc_positions. A position removed from config still has rows
    // here, which is exactly the "closed a symbol that's no longer configured" case the join must
    // not silently drop -- hence the LEFT JOIN rather than an inner one.
    const events = session === null
      ? []
      : db
          .prepare<[string], Record<string, unknown>>(
            `SELECT p.symbol AS symbol, e.action AS action, e.reason AS reason, e.executed AS executed,
                    e.gate AS gate, COUNT(*) AS n
               FROM pmcc_management_events e
               LEFT JOIN pmcc_positions p ON p.position_id = e.position_id
              WHERE e.session_date = ?
              GROUP BY p.symbol, e.action, e.reason, e.executed, e.gate ORDER BY n DESC`,
          )
          .all(session)
          .map((r) => ({
            symbol: str(r["symbol"]),
            action: str(r["action"]) ?? "",
            reason: str(r["reason"]) ?? "",
            executed: r["executed"] === 1,
            gate: str(r["gate"]),
            n: Number(r["n"] ?? 0),
          }));

    const iteration = db
      .prepare<[], Record<string, unknown>>(
        "SELECT ran_at, phase, status FROM pmcc_loop_iterations ORDER BY ran_at DESC LIMIT 1",
      )
      .get();
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);

    const breaks = db
      .prepare<[], Record<string, unknown>>(
        "SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC",
      )
      .all()
      .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "", note: str(r["note"]) }));

    return {
      session,
      dbPresent: true,
      openPositions,
      openCount: openPositions.length,
      books: readBooks(db),
      integrity: {
        exposure: {
          positionsWithExposure: exposureRows.filter((e) => e.exposed > 0).length,
          exposedTicks: exposureRows.reduce((s, e) => s + e.exposed, 0),
          markedTicks: exposureRows.reduce((s, e) => s + e.marked, 0),
        },
        dividends: symbols.map((symbol) => {
          const declared = params.dividends[symbol];
          return {
            symbol,
            declaredThrough: declared?.declaredThrough ?? null,
            exDates: declared?.exDates ?? [],
            refreshDue: dividendRefreshDue(declared?.declaredThrough ?? null, session),
          };
        }),
        markCoverage: readMarkCoverage(db, session),
        schemaDrift: schemaDrift(db),
        measurementBreaks: breaks,
      },
      today: {
        attempts,
        events,
        lastIteration:
          iteration === undefined || ranAt === null
            ? null
            : {
                ranAt,
                phase: str(iteration["phase"]) ?? "",
                status: str(iteration["status"]) ?? "",
                ageSeconds: Math.max(0, Date.now() / 1000 - ranAt),
              },
      },
      params: {
        tvCloseThreshold: params.tvCloseThreshold,
        tvManagedExit: params.tvManagedExit,
        assignmentExposureTv: params.assignmentExposureTv,
        longDeltaMin: params.longDeltaMin,
        longDeltaMax: params.longDeltaMax,
        symbols,
        settlementStyle: params.settlementStyle,
      },
    };
  });
}

export interface PmccHistoryFilter {
  book: string | null;
  symbol: string | null;
}

/**
 * Completed cycles, newest first.
 *
 * `short_settled` rows are included deliberately. That status means the short expired ITM and its
 * delivered shares are waiting for the next session's disposal — the short cycle IS finished, and
 * omitting it would make the table silently lose a position for a day, which reads as nothing
 * having happened. It carries a null net until disposal, and the page badges it rather than
 * pretending the number is late.
 */
export function readPmccHistory(
  config: ConsoleConfig,
  filter: PmccHistoryFilter,
  page: PageRequest = FIRST_PAGE,
): Paged<PmccCycleRow> {
  const clauses = ["status IN ('closed', 'short_settled')"];
  const params: string[] = [];
  if (filter.book !== null) {
    clauses.push("book = ?");
    params.push(filter.book);
  }
  if (filter.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }

  return withReadOnlyDb<Paged<PmccCycleRow>>(dbPath(config), emptyPage(page), (db) => {
    const rows = pagedQuery<PmccCycleRow>(
      db,
      {
        columns: `position_id, symbol, book, entry_session, closed_session, status, exit_reason,
                  long_strike, long_expiration, entry_spot, settlement_spot, net_debit, entry_net_tv,
                  entry_weekly_yield_pct, roll_count, itm_settlements, gross_pnl, fees,
                  entry_cost, exit_cost, entry_slippage, exit_slippage`,
        from: "pmcc_positions",
        where: clauses.join(" AND "),
        params,
        orderBy: "entry_session DESC, id DESC",
      },
      page,
      (r) => {
        const gross = num(r["gross_pnl"]);
        const fees = num(r["fees"]);
        return {
          positionId: str(r["position_id"]) ?? "",
          symbol: str(r["symbol"]) ?? "",
          book: str(r["book"]) ?? "",
          entrySession: str(r["entry_session"]) ?? "",
          closedSession: str(r["closed_session"]),
          status: str(r["status"]) ?? "",
          exitReason: str(r["exit_reason"]),
          longStrike: num(r["long_strike"]),
          longExpiration: str(r["long_expiration"]),
          entrySpot: num(r["entry_spot"]),
          settlementSpot: num(r["settlement_spot"]),
          netDebit: num(r["net_debit"]),
          entryNetTv: num(r["entry_net_tv"]),
          entryWeeklyYieldPct: num(r["entry_weekly_yield_pct"]),
          rollCount: num(r["roll_count"]),
          itmSettlements: num(r["itm_settlements"]),
          grossPnl: gross,
          fees,
          // Null propagates: an unrecorded gross or fee is not a zero-cost trade.
          netPnl: gross === null || fees === null ? null : gross - fees,
          entryCost: num(r["entry_cost"]),
          exitCost: num(r["exit_cost"]),
          entrySlippage: num(r["entry_slippage"]),
          exitSlippage: num(r["exit_slippage"]),
          entryMaxSpreadPct: null,
          entryMaxSpreadAbs: null,
          shorts: [],
          rolls: [],
          assignments: [],
        };
      },
    );

    // Detail for THIS PAGE's positions only — three small queries beat one join fanning three ways.
    const ids = rows.rows.map((r) => r.positionId);
    if (ids.length === 0) return rows;
    const marks = ids.map(() => "?").join(", ");

    const shortsBy = new Map<string, PmccShortLeg[]>();
    for (const r of db
      .prepare<string[], Record<string, unknown>>(
        `SELECT position_id, leg_role, strike, expiration, close_kind, close_value FROM pmcc_legs
          WHERE position_id IN (${marks}) AND leg_role != 'long_call' ORDER BY id`,
      )
      .all(...ids)) {
      const id = str(r["position_id"]) ?? "";
      const list = shortsBy.get(id) ?? [];
      list.push({
        legRole: str(r["leg_role"]) ?? "",
        strike: num(r["strike"]),
        expiration: str(r["expiration"]),
        closeKind: str(r["close_kind"]),
        closeValue: num(r["close_value"]),
      });
      shortsBy.set(id, list);
    }

    const rollsBy = new Map<string, PmccRoll[]>();
    for (const r of db
      .prepare<string[], Record<string, unknown>>(
        `SELECT position_id, session_date, detail_json FROM pmcc_management_events
          WHERE action = 'roll_short' AND executed = 1 AND position_id IN (${marks}) ORDER BY occurred_at`,
      )
      .all(...ids)) {
      const id = str(r["position_id"]) ?? "";
      let detail: Record<string, unknown> = {};
      try {
        const raw = str(r["detail_json"]);
        if (raw !== null) detail = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        // A malformed detail blob costs the roll's numbers, never the roll's existence.
      }
      const list = rollsBy.get(id) ?? [];
      list.push({
        session: str(r["session_date"]),
        oldStrike: num(detail["old_strike"]),
        newStrike: num(detail["new_strike"]),
        oldExpiration: str(detail["old_expiration"]),
        newExpiration: str(detail["new_expiration"]),
        netRollCredit: num(detail["net_roll_credit"]),
      });
      rollsBy.set(id, list);
    }

    const assignmentsBy = new Map<string, PmccAssignment[]>();
    for (const r of db
      .prepare<string[], Record<string, unknown>>(
        `SELECT position_id, leg_role, direction, shares, basis, strike, status,
                disposed_session, disposal_price, share_pnl
           FROM pmcc_assignments WHERE position_id IN (${marks}) ORDER BY id`,
      )
      .all(...ids)) {
      const id = str(r["position_id"]) ?? "";
      const list = assignmentsBy.get(id) ?? [];
      list.push({
        legRole: str(r["leg_role"]) ?? "",
        direction: str(r["direction"]) ?? "",
        shares: num(r["shares"]),
        basis: num(r["basis"]),
        strike: num(r["strike"]),
        status: str(r["status"]) ?? "",
        disposedSession: str(r["disposed_session"]),
        disposalPrice: num(r["disposal_price"]),
        sharePnl: num(r["share_pnl"]),
      });
      assignmentsBy.set(id, list);
    }

    const spreads = entrySpreadByPosition(db, ids);

    return {
      ...rows,
      rows: rows.rows.map((r) => ({
        ...r,
        entryMaxSpreadPct: spreads.get(r.positionId)?.pct ?? null,
        entryMaxSpreadAbs: spreads.get(r.positionId)?.abs ?? null,
        shorts: shortsBy.get(r.positionId) ?? [],
        rolls: rollsBy.get(r.positionId) ?? [],
        assignments: assignmentsBy.get(r.positionId) ?? [],
      })),
    };
  });
}

/** Open (undisposed) delivered-share rows — the shares the module is currently short over a weekend. */
export function readPmccAssignments(config: ConsoleConfig): Array<PmccAssignment & { positionId: string; symbol: string; assignedSession: string }> {
  return withReadOnlyDb(dbPath(config), [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT position_id, symbol, leg_role, assigned_session, direction, shares, basis, strike,
                status, disposed_session, disposal_price, share_pnl
           FROM pmcc_assignments ORDER BY assigned_session DESC, id DESC`,
      )
      .all()
      .map((r) => ({
        positionId: str(r["position_id"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        assignedSession: str(r["assigned_session"]) ?? "",
        legRole: str(r["leg_role"]) ?? "",
        direction: str(r["direction"]) ?? "",
        shares: num(r["shares"]),
        basis: num(r["basis"]),
        strike: num(r["strike"]),
        status: str(r["status"]) ?? "",
        disposedSession: str(r["disposed_session"]),
        disposalPrice: num(r["disposal_price"]),
        sharePnl: num(r["share_pnl"]),
      })),
  );
}

/** The history filter's own options. No era mechanism: the module has one era and one week of data. */
export function readPmccMeta(config: ConsoleConfig): PmccMeta {
  const empty: PmccMeta = { books: [], symbols: [], sessions: [] };
  return withReadOnlyDb<PmccMeta>(dbPath(config), empty, (db) => {
    const column = (name: string, table: string): string[] =>
      db
        .prepare<[], Record<string, unknown>>(`SELECT DISTINCT ${name} AS v FROM ${table} ORDER BY ${name}`)
        .all()
        .map((r) => str(r["v"]) ?? "")
        .filter((v) => v !== "");
    return {
      books: column("book", "pmcc_positions"),
      symbols: column("symbol", "pmcc_positions"),
      sessions: column("entry_session", "pmcc_positions").reverse(),
    };
  });
}
