import path from "node:path";
import type {
  CurveBookCell,
  CurveCycleRow,
  CurveFlipDivergence,
  CurveMeta,
  CurveOpenPosition,
  CurvePayload,
  CurveRegimeRow,
  Paged,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, obj, readJson, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { emptyPage, pagedQuery, FIRST_PAGE, type PageRequest } from "./paging.js";

/**
 * curve's read layer.
 *
 * Paper only, and that is structural rather than a default: the module has no live loop and no live
 * DB, so there is one store to read and no `mode` anywhere in this file, exactly the pmcc/calendars
 * reasoning (packages/curve/CLAUDE.md's Live-trading prerequisites section).
 *
 * Every query here mirrors `packages/curve/src/cherrypick/curve/analytics.py`, the module's stated
 * ONE query layer. That layer is Python and this one is TypeScript, so the two cannot share code --
 * the mirroring is a discipline, and each function below names the analytics function it answers
 * for. `None` never means zero: a position with no usable mark reports a null close cost, not $0.00,
 * because "not recorded" and "was zero" are different facts.
 *
 * The module's own honesty framing decides what this file is obliged to surface: `flip_divergence`
 * is the real comparison sample for control-vs-noflip (not raw trade count), the regime series'
 * value is its continuity (a session with no trade still gets a row), and early assignment exposure
 * is measured, never modelled -- the paper net is an upper bound.
 */

const DB_FILE = "paper_trades.db";

interface CurveParams {
  contangoMax: number | null;
  hookThreshold: number | null;
  profitTakePct: number | null;
  closeDte: number | null;
  assignmentExposureTv: number | null;
}

/**
 * Columns this console build knows, per migrated table -- the TypeScript half of the module's
 * `db.stale_writer_columns` guard (pmcc's reader carries the same one, for the same reason).
 * REFRESH THIS when curve's `db.py` gains a column.
 */
const KNOWN_COLUMNS: Record<string, string[]> = {
  curve_positions: [
    "id", "position_id", "symbol", "book", "entry_session", "quantity", "expiration", "short_strike",
    "long_strike", "entry_time", "entry_spot", "entry_short_mid", "entry_long_mid", "entry_credit",
    "entry_width", "entry_max_loss", "entry_credit_pct_of_width", "entry_short_delta",
    "short_selected_by", "entry_dte", "entry_ratio", "entry_regime", "entry_hook", "entry_cost",
    "entry_slippage", "advice_params", "exposure_ticks", "status", "exit_reason", "closed_at",
    "closed_session", "exit_value", "exit_cost", "exit_slippage", "settlement_spot",
    "itm_settlements", "gross_pnl", "fees", "created_at", "updated_at",
  ],
  curve_legs: [
    "id", "position_id", "leg_role", "occ_symbol", "streamer_symbol", "expiration", "strike",
    "option_type", "action", "quantity", "entry_bid", "entry_ask", "entry_mid", "entry_iv",
    "entry_delta", "status", "close_kind", "closed_at", "close_bid", "close_ask", "close_value",
    "created_at", "updated_at",
  ],
  curve_marks: [
    "id", "position_id", "leg_role", "marked_at", "session_date", "bid", "ask", "mid", "delta", "iv",
    "spot", "close_cost", "short_tv", "assignment_exposed", "quote_age_s", "usable", "refusal",
  ],
  curve_regime: [
    "id", "trade_date", "tick", "recorded_at", "ratio", "regime", "hook", "vix", "vix3m",
    "vix_age_s", "vix3m_age_s", "usable", "refusal", "created_at", "updated_at",
  ],
  curve_assignments: [
    "id", "position_id", "leg_role", "symbol", "assigned_session", "assigned_at", "direction",
    "shares", "basis", "strike", "option_type", "status", "disposed_session", "disposed_at",
    "disposal_price", "share_pnl", "fees", "created_at", "updated_at",
  ],
};

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.curveDir, DB_FILE);
}

/** The same session `latestSession` resolves for every other card on this page, exposed for
 *  readers outside this file (the decisions card) that need it without duplicating the fallback
 *  chain -- see pmcc's `resolvePmccSession` for the incident this pattern exists to prevent. */
export function resolveCurveSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(dbPath(config), null, (db) => latestSession(db));
}

/** The module's declared knobs, resolved the way the module itself resolves them (deployed config
 * first, then the repo's, then the shipped example). Missing config degrades to null rather than
 * failing the page. */
function loadParams(config: ConsoleConfig): CurveParams {
  let doc: Record<string, unknown> | null = null;
  for (const candidate of config.paths.curveConfigCandidates) {
    doc = readJson(candidate);
    if (doc !== null) break;
  }
  const defaults = obj(doc?.["defaults"]);
  return {
    contangoMax: num(defaults["contango_max"]),
    hookThreshold: num(defaults["hook_threshold"]),
    profitTakePct: num(defaults["profit_take_pct"]),
    closeDte: num(defaults["close_dte"]),
    assignmentExposureTv: num(defaults["assignment_exposure_tv"]),
  };
}

/** The session every card on the page names -- the loop's own iterations first, the pmcc reasoning
 * verbatim: the loop ticks on days that take no position at all, and `curve_regime` is written every
 * session too, so either is a fair fallback naming "the last day the module RAN". */
function latestSession(db: DatabaseHandle): string | null {
  const fromLoop = db
    .prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM curve_loop_iterations")
    .get()?.d;
  if (fromLoop != null) return fromLoop;
  const fromRegime = db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM curve_regime").get()?.d;
  if (fromRegime != null) return fromRegime;
  return db.prepare<[], { d: string | null }>("SELECT MAX(entry_session) AS d FROM curve_positions").get()?.d ?? null;
}

/** Mirrors `analytics.exposure()`: exposed and marked tick counts per position. */
function exposureByPosition(db: DatabaseHandle): Map<string, { exposed: number; marked: number }> {
  const out = new Map<string, { exposed: number; marked: number }>();
  const rows = db
    .prepare<[], Record<string, unknown>>(
      `SELECT position_id,
              SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed,
              COUNT(*) AS marked
         FROM curve_marks
        WHERE short_tv IS NOT NULL AND usable = 1
        GROUP BY position_id`,
    )
    .all();
  for (const r of rows) {
    out.set(str(r["position_id"]) ?? "", { exposed: Number(r["exposed"] ?? 0), marked: Number(r["marked"] ?? 0) });
  }
  return out;
}

/** Mirrors `analytics.worksheet()` -- open positions plus their latest usable close-cost mark. */
/**
 * Mark-to-market P&L for a credit spread whose marks carry a whole-structure `close_cost`.
 *
 * The same convention `readers/unrealised.ts` implements per-leg for pmcc and calendars, reached by
 * a shorter route: `close_cost` is the SIGNED net to unwind every leg at mid (negative for a credit
 * structure you must buy back), so the credit received plus that is the position's mark. Kept here
 * rather than in the shared helper because the input differs -- curve's mark table precomputes what
 * the others leave per-leg -- and folding two different inputs behind one name would hide that.
 *
 * `fees` is costs INCURRED so far; no settlement fee is in it because settlement has not happened.
 */
function creditUnrealised(
  p: Record<string, unknown>,
  closeCost: number | null,
): { unrealisedGross: number | null; unrealisedNet: number | null; feesToDate: number | null } {
  const credit = num(p["entry_credit"]);
  const qty = num(p["quantity"]) ?? 1;
  const fees = num(p["fees"]);
  if (credit === null || closeCost === null) {
    return { unrealisedGross: null, unrealisedNet: null, feesToDate: fees };
  }
  const gross = Math.round((credit + closeCost) * 100 * qty * 100) / 100;
  return {
    unrealisedGross: gross,
    unrealisedNet: fees === null ? null : Math.round((gross - fees) * 100) / 100,
    feesToDate: fees,
  };
}

function readOpenPositions(db: DatabaseHandle): CurveOpenPosition[] {
  const exposure = exposureByPosition(db);
  const latestMark = db.prepare<[string], Record<string, unknown>>(
    `SELECT close_cost, spot, marked_at FROM curve_marks
      WHERE position_id = ? AND close_cost IS NOT NULL AND usable = 1
      ORDER BY marked_at DESC LIMIT 1`,
  );
  return db
    .prepare<[], Record<string, unknown>>("SELECT * FROM curve_positions WHERE status != 'closed' ORDER BY symbol, book")
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
        shortStrike: num(p["short_strike"]),
        longStrike: num(p["long_strike"]),
        expiration: str(p["expiration"]),
        entrySpot: num(p["entry_spot"]),
        entryCredit: num(p["entry_credit"]),
        entryWidth: num(p["entry_width"]),
        entryMaxLoss: num(p["entry_max_loss"]),
        entryCreditPctOfWidth: num(p["entry_credit_pct_of_width"]),
        entryRatio: num(p["entry_ratio"]),
        entryRegime: str(p["entry_regime"]),
        entryHook: p["entry_hook"] === 1,
        exposureTicks: num(p["exposure_ticks"]),
        currentCloseCost: mark === undefined ? null : num(mark["close_cost"]),
        currentSpot: mark === undefined ? null : num(mark["spot"]),
        entrySession: str(p["entry_session"]) ?? "",
        ...creditUnrealised(p, mark === undefined ? null : num(mark["close_cost"])),
      };
    });
}

/** Mirrors `analytics.headline()`: per-book, per-symbol results over CLOSED positions. */
function readBooks(db: DatabaseHandle): CurveBookCell[] {
  return db
    .prepare<[], Record<string, unknown>>(
      `SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees,
              SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins
         FROM curve_positions WHERE status = 'closed'
        GROUP BY book, symbol ORDER BY book, symbol`,
    )
    .all()
    .map((r) => {
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
      };
    });
}

/**
 * Mirrors `analytics.flip_divergence()`: how many (symbol, entry_session) pairs saw `control` close
 * on `regime_flip` while `noflip` held past that point -- the noflip comparison's EFFECTIVE sample,
 * per the module's own honesty rule. Until a flip fires, control and noflip are byte-identical by
 * construction, so raw trade count is the wrong denominator for "what did the flip rule do".
 */
function readFlipDivergence(db: DatabaseHandle): CurveFlipDivergence {
  const rows = db
    .prepare<[], Record<string, unknown>>(
      "SELECT symbol, entry_session FROM curve_positions WHERE book = 'control' AND exit_reason = 'regime_flip'",
    )
    .all();
  const heldPast = db.prepare<[string, string], { hit: number }>(
    `SELECT 1 AS hit FROM curve_positions WHERE book = 'noflip' AND symbol = ? AND entry_session = ?
      AND (exit_reason != 'regime_flip' OR exit_reason IS NULL)`,
  );
  let diverged = 0;
  for (const r of rows) {
    const symbol = str(r["symbol"]) ?? "";
    const session = str(r["entry_session"]) ?? "";
    if (heldPast.get(symbol, session) !== undefined) diverged += 1;
  }
  return {
    flipDivergenceCount: diverged,
    controlFlipExits: rows.length,
    note:
      "the noflip comparison's effective sample is this count, not the trade count -- " +
      "control and noflip are identical until a flip actually fires",
  };
}

/** Mirrors `analytics.regime_series()`: the most recent rows, oldest first. */
function readRegimeSeries(db: DatabaseHandle, limit = 60): CurveRegimeRow[] {
  return db
    .prepare<[number], Record<string, unknown>>("SELECT * FROM curve_regime ORDER BY trade_date DESC LIMIT ?")
    .all(limit)
    .reverse()
    .map((r) => ({
      tradeDate: str(r["trade_date"]) ?? "",
      ratio: num(r["ratio"]),
      regime: str(r["regime"]),
      hook: r["hook"] === null ? null : r["hook"] === 1,
      vix: num(r["vix"]),
      vix3m: num(r["vix3m"]),
      usable: r["usable"] === 1,
      refusal: str(r["refusal"]),
    }));
}

/** Mirrors `analytics.mark_coverage()` for one session. */
function readMarkCoverage(db: DatabaseHandle, session: string | null): CurvePayload["integrity"]["markCoverage"] {
  if (session === null) return { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] };
  const totals = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM curve_marks WHERE session_date = ?",
    )
    .get(session);
  const marks = Number(totals?.["total"] ?? 0);
  const refused = Number(totals?.["refused"] ?? 0);
  const refusals = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT refusal, COUNT(*) AS n FROM curve_marks
        WHERE session_date = ? AND usable = 0 AND refusal IS NOT NULL
        GROUP BY refusal ORDER BY n DESC`,
    )
    .all(session)
    .map((r) => ({ reason: str(r["refusal"]) ?? "", n: Number(r["n"] ?? 0) }));
  return { session, marks, refused, refusalShare: marks > 0 ? refused / marks : null, refusals };
}

/** Whether today's regime row exists and is usable -- the series' own continuity check, and the
 * module's rule that a session is written whether or not any book traded (its second product). */
function readRegimeToday(db: DatabaseHandle, session: string | null): CurvePayload["integrity"]["regimeToday"] {
  if (session === null) return { present: false, usable: false, refusal: null };
  const row = db
    .prepare<[string], Record<string, unknown>>("SELECT usable, refusal FROM curve_regime WHERE trade_date = ?")
    .get(session);
  if (row === undefined) return { present: false, usable: false, refusal: null };
  return { present: true, usable: row["usable"] === 1, refusal: str(row["refusal"]) };
}

/** Columns the ledger has that this build does not know -- see KNOWN_COLUMNS. */
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

export function readCurve(config: ConsoleConfig): CurvePayload {
  const params = loadParams(config);
  const empty: CurvePayload = {
    session: null,
    dbPresent: false,
    openPositions: [],
    openCount: 0,
    books: [],
    flipDivergence: {
      flipDivergenceCount: 0,
      controlFlipExits: 0,
      note: "the noflip comparison's effective sample is this count, not the trade count -- control and noflip are identical until a flip actually fires",
    },
    regimeSeries: [],
    integrity: {
      exposure: { positionsWithExposure: 0, exposedTicks: 0, markedTicks: 0 },
      markCoverage: { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] },
      regimeToday: { present: false, usable: false, refusal: null },
      schemaDrift: [],
      measurementBreaks: [],
    },
    today: { lastIteration: null },
    params,
  };

  return withReadOnlyDb<CurvePayload>(dbPath(config), empty, (db) => {
    const session = latestSession(db);
    const openPositions = readOpenPositions(db);
    const exposureRows = [...exposureByPosition(db).values()];

    const iteration = db
      .prepare<[], Record<string, unknown>>(
        "SELECT ran_at, phase, status FROM curve_loop_iterations ORDER BY ran_at DESC LIMIT 1",
      )
      .get();
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);

    const breaks = db
      .prepare<[], Record<string, unknown>>("SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC")
      .all()
      .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "", note: str(r["note"]) }));

    return {
      session,
      dbPresent: true,
      openPositions,
      openCount: openPositions.length,
      books: readBooks(db),
      flipDivergence: readFlipDivergence(db),
      regimeSeries: readRegimeSeries(db),
      integrity: {
        exposure: {
          positionsWithExposure: exposureRows.filter((e) => e.exposed > 0).length,
          exposedTicks: exposureRows.reduce((s, e) => s + e.exposed, 0),
          markedTicks: exposureRows.reduce((s, e) => s + e.marked, 0),
        },
        markCoverage: readMarkCoverage(db, session),
        regimeToday: readRegimeToday(db, session),
        schemaDrift: schemaDrift(db),
        measurementBreaks: breaks,
      },
      today: {
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
      params,
    };
  });
}

export interface CurveHistoryFilter {
  book: string | null;
  symbol: string | null;
}

/** Completed cycles, newest first. */
export function readCurveHistory(
  config: ConsoleConfig,
  filter: CurveHistoryFilter,
  page: PageRequest = FIRST_PAGE,
): Paged<CurveCycleRow> {
  const clauses = ["status = 'closed'"];
  const params: string[] = [];
  if (filter.book !== null) {
    clauses.push("book = ?");
    params.push(filter.book);
  }
  if (filter.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }

  return withReadOnlyDb<Paged<CurveCycleRow>>(dbPath(config), emptyPage(page), (db) =>
    pagedQuery<CurveCycleRow>(
      db,
      {
        columns: `position_id, symbol, book, entry_session, closed_session, status, exit_reason,
                  short_strike, long_strike, expiration, entry_spot, settlement_spot, entry_credit,
                  entry_width, entry_ratio, entry_regime, entry_hook, gross_pnl, fees`,
        from: "curve_positions",
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
          shortStrike: num(r["short_strike"]),
          longStrike: num(r["long_strike"]),
          expiration: str(r["expiration"]),
          entrySpot: num(r["entry_spot"]),
          settlementSpot: num(r["settlement_spot"]),
          entryCredit: num(r["entry_credit"]),
          entryWidth: num(r["entry_width"]),
          entryRatio: num(r["entry_ratio"]),
          entryRegime: str(r["entry_regime"]),
          entryHook: r["entry_hook"] === 1,
          grossPnl: gross,
          fees,
          netPnl: gross === null || fees === null ? null : gross - fees,
        };
      },
    ),
  );
}

/** The history filter's own options. No era mechanism: the module has one era and no pooled data yet. */
export function readCurveMeta(config: ConsoleConfig): CurveMeta {
  const empty: CurveMeta = { books: [], symbols: [], sessions: [] };
  return withReadOnlyDb<CurveMeta>(dbPath(config), empty, (db) => {
    const column = (name: string, table: string): string[] =>
      db
        .prepare<[], Record<string, unknown>>(`SELECT DISTINCT ${name} AS v FROM ${table} ORDER BY ${name}`)
        .all()
        .map((r) => str(r["v"]) ?? "")
        .filter((v) => v !== "");
    return {
      books: column("book", "curve_positions"),
      symbols: column("symbol", "curve_positions"),
      sessions: column("entry_session", "curve_positions").reverse(),
    };
  });
}
