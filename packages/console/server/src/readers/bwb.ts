import path from "node:path";
import type {
  BwbBookCell,
  BwbCycleRow,
  BwbEntryAttempt,
  BwbFireCount,
  BwbManagementEvent,
  BwbMeta,
  BwbOpenPosition,
  BwbPayload,
  Paged,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { emptyPage, pagedQuery, FIRST_PAGE, type PageRequest } from "./paging.js";

/**
 * bwb's read layer.
 *
 * Paper only, and that is structural rather than a default: the module has no live loop and no live
 * DB (packages/bwb/CLAUDE.md's Guardrails section), the same pmcc/calendars/curve reasoning -- no
 * `mode` anywhere in this file.
 *
 * Every query here mirrors `packages/bwb/src/cherrypick/bwb/analytics.py`, the module's stated ONE
 * query layer. That layer is Python and this one is TypeScript, so the two cannot share code -- the
 * mirroring is a discipline, and each function below names the analytics function it answers for.
 * `None` never means zero: a position with no usable mark reports a null close cost, not $0.00.
 *
 * The module's own honesty framing decides what this file is obliged to surface: the effective
 * sample for an arm-vs-control comparison is that arm's FIRE COUNT (`analytics.fire_counts`), not
 * its trade count, and the daily-ladder correlation caveat travels beside those counts rather than
 * being buried in prose only the HelpTab shows.
 */

const DB_FILE = "paper_trades.db";

const CORRELATION_CAVEAT =
  "concurrent positions share regime context -- one sharp selloff can fire the same trigger across " +
  "several overlapping positions in one session. Rows are not independent samples; the honest unit " +
  "for 'how often does this trigger help' is closer to distinct fire episodes than fired positions.";

/**
 * Columns this console build knows, per migrated table -- the TypeScript half of the module's
 * `db.stale_writer_columns` guard (pmcc/curve's readers carry the same one, for the same reason).
 * REFRESH THIS when bwb's `db.py` gains a column.
 */
const KNOWN_COLUMNS: Record<string, string[]> = {
  bwb_positions: [
    "id", "position_id", "symbol", "book", "entry_session", "structure_signature", "quantity",
    "expiration", "body_strike", "near_strike", "far_strike", "entry_time", "entry_spot",
    "entry_atm_strike", "entry_expected_move", "entry_body_mid", "entry_near_mid", "entry_far_mid",
    "entry_credit", "entry_narrow_width", "entry_wide_width", "entry_max_loss", "entry_dte",
    "entry_cost", "entry_slippage", "advice_params", "peak_abs_delta", "below_flip_seen", "armed_at",
    "arm_reason", "addon_fired_at", "addon_short_strike", "addon_long_strike", "addon_credit",
    "addon_cost", "addon_slippage", "status", "exit_reason", "closed_at", "closed_session",
    "settlement_spot", "itm_settlements", "gross_pnl", "fees", "created_at", "updated_at",
  ],
  bwb_legs: [
    "id", "position_id", "leg_role", "occ_symbol", "streamer_symbol", "expiration", "strike",
    "option_type", "action", "quantity", "entry_bid", "entry_ask", "entry_mid", "entry_iv",
    "entry_delta", "status", "close_kind", "closed_at", "close_bid", "close_ask", "close_value",
    "created_at", "updated_at",
  ],
  bwb_marks: [
    "id", "position_id", "leg_role", "marked_at", "session_date", "bid", "ask", "mid", "delta", "iv",
    "spot", "close_cost", "quote_age_s", "usable", "refusal",
  ],
  bwb_trigger_ticks: [
    "id", "entry_session", "structure_signature", "symbol", "ticked_at", "session_date",
    "near_abs_delta", "peak_abs_delta", "spot", "gamma_flip", "gamma_flip_basis", "below_flip_seen",
    "addon_short_bid", "addon_short_ask", "addon_long_bid", "addon_long_ask", "measured", "refusal",
    "spot_measured", "flip_measured",
  ],
};

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.bwbDir, DB_FILE);
}

/** The same session `latestSession` resolves for every other card on this page, exposed for
 *  readers outside this file (the decisions card) that need it without duplicating the fallback
 *  chain -- see pmcc's `resolvePmccSession` for the incident this pattern exists to prevent. */
export function resolveBwbSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(dbPath(config), null, (db) => latestSession(db));
}

/** The session every card on the page names -- the loop's own iterations first, the pmcc/curve
 * reasoning verbatim: the loop ticks on days that take no position at all. */
function latestSession(db: DatabaseHandle): string | null {
  const fromLoop = db
    .prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM bwb_loop_iterations")
    .get()?.d;
  if (fromLoop != null) return fromLoop;
  return db.prepare<[], { d: string | null }>("SELECT MAX(entry_session) AS d FROM bwb_positions").get()?.d ?? null;
}

/** Mirrors `analytics.worksheet()`. */
function readOpenPositions(db: DatabaseHandle): BwbOpenPosition[] {
  const latestMark = db.prepare<[string], Record<string, unknown>>(
    `SELECT close_cost, spot, marked_at FROM bwb_marks
      WHERE position_id = ? AND close_cost IS NOT NULL AND usable = 1
      ORDER BY marked_at DESC LIMIT 1`,
  );
  return db
    .prepare<[], Record<string, unknown>>("SELECT * FROM bwb_positions WHERE status != 'closed' ORDER BY symbol, book")
    .all()
    .map((p) => {
      const positionId = str(p["position_id"]) ?? "";
      const mark = latestMark.get(positionId);
      return {
        positionId,
        symbol: str(p["symbol"]) ?? "",
        book: str(p["book"]) ?? "",
        status: str(p["status"]) ?? "",
        bodyStrike: num(p["body_strike"]),
        nearStrike: num(p["near_strike"]),
        farStrike: num(p["far_strike"]),
        expiration: str(p["expiration"]),
        entrySpot: num(p["entry_spot"]),
        entryCredit: num(p["entry_credit"]),
        entryMaxLoss: num(p["entry_max_loss"]),
        peakAbsDelta: num(p["peak_abs_delta"]),
        belowFlipSeen: p["below_flip_seen"] === 1,
        armedAt: str(p["armed_at"]),
        addonFiredAt: str(p["addon_fired_at"]),
        addonShortStrike: num(p["addon_short_strike"]),
        addonLongStrike: num(p["addon_long_strike"]),
        addonCredit: num(p["addon_credit"]),
        currentCloseCost: mark === undefined ? null : num(mark["close_cost"]),
        currentSpot: mark === undefined ? null : num(mark["spot"]),
        entrySession: str(p["entry_session"]) ?? "",
        ...unrealised(p, mark === undefined ? null : num(mark["close_cost"])),
      };
    });
}

/**
 * Mark-to-market P&L for an OPEN position, in the module's own convention.
 *
 * `book.py` states it: "`gross_pnl` is mid-priced and cost-free (per-leg P&L x100 xqty); `fees` is
 * the TOTAL modeled cost (entry + addon entry + settlement); net is always `gross_pnl - fees`."
 * This mirrors that, substituting the mark-to-market gross for the settled one, so an open row and
 * a closed row mean the same thing by the same arithmetic rather than by two definitions that
 * happen to agree.
 *
 * Gross is `(entry credit + add-on credit + cost to close) x 100 x quantity`. `close_cost` is the
 * SIGNED net to unwind EVERY leg at mid, the add-on's included, while `entry_credit` covers only
 * the original fly -- so the add-on credit has to be added back explicitly or a fired position is
 * charged for unwinding legs whose credit was never counted. Caught on the 2026-08-28 cohort, where
 * that omission put the fired `delta` book at -527.83 beside four identical siblings at -146.89.
 *
 * `fees` on an open row is what has been INCURRED so far (entry + any add-on entry); the settlement
 * fee is not in it because settlement has not happened. So net here is net of costs to date, not of
 * the round trip -- stated on the column rather than left for a reader to assume either way.
 */
function unrealised(
  p: Record<string, unknown>,
  closeCost: number | null,
): { unrealisedGross: number | null; unrealisedNet: number | null; feesToDate: number | null } {
  const credit = num(p["entry_credit"]);
  const addon = num(p["addon_credit"]) ?? 0;
  const qty = num(p["quantity"]) ?? 1;
  const fees = num(p["fees"]);
  if (credit === null || closeCost === null) {
    // No usable mark is not a zero P&L, and never a zero one dressed as a number.
    return { unrealisedGross: null, unrealisedNet: null, feesToDate: fees };
  }
  const gross = (credit + addon + closeCost) * 100 * qty;
  return {
    unrealisedGross: Math.round(gross * 100) / 100,
    unrealisedNet: fees === null ? null : Math.round((gross - fees) * 100) / 100,
    feesToDate: fees,
  };
}

/** Mirrors `analytics.headline()`'s book breakdown. */
function readBooks(db: DatabaseHandle): BwbBookCell[] {
  return db
    .prepare<[], Record<string, unknown>>(
      `SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees,
              SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins
         FROM bwb_positions WHERE status = 'closed'
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

/** Mirrors `analytics.fire_counts()`: the real effective sample per arm, trade count vs fire count. */
function readFireCounts(db: DatabaseHandle): BwbFireCount[] {
  return db
    .prepare<[], Record<string, unknown>>(
      "SELECT book, COUNT(*) AS n, SUM(addon_fired_at IS NOT NULL) AS fired FROM bwb_positions GROUP BY book ORDER BY book",
    )
    .all()
    .map((r) => {
      const n = Number(r["n"] ?? 0);
      const fired = Number(r["fired"] ?? 0);
      return { book: str(r["book"]) ?? "", positions: n, fired, fireRate: n > 0 ? fired / n : null };
    });
}

const EMPTY_TRIGGER_COVERAGE: BwbPayload["integrity"]["triggerCoverage"] = {
  session: null,
  ticks: 0,
  refused: 0,
  refusalShare: null,
  noSpot: 0,
  noFlip: 0,
  reasons: {},
  totalFailure: false,
};

/** Mirrors `analytics.trigger_coverage()` for one session. */
function readTriggerCoverage(db: DatabaseHandle, session: string | null): BwbPayload["integrity"]["triggerCoverage"] {
  if (session === null) return EMPTY_TRIGGER_COVERAGE;
  const row = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT COUNT(*) AS total, SUM(measured = 0) AS refused,
              SUM(spot_measured = 0) AS no_spot, SUM(flip_measured = 0) AS no_flip
       FROM bwb_trigger_ticks WHERE session_date = ?`,
    )
    .get(session);
  const ticks = Number(row?.["total"] ?? 0);
  const refused = Number(row?.["refused"] ?? 0);
  const reasons: Record<string, number> = {};
  for (const r of db
    .prepare<[string], Record<string, unknown>>(
      `SELECT refusal, COUNT(*) AS n FROM bwb_trigger_ticks
       WHERE session_date = ? AND measured = 0 AND refusal IS NOT NULL
       GROUP BY refusal ORDER BY n DESC`,
    )
    .all(session)) {
    reasons[String(r["refusal"])] = Number(r["n"] ?? 0);
  }
  return {
    session,
    ticks,
    refused,
    refusalShare: ticks > 0 ? refused / ticks : null,
    noSpot: Number(row?.["no_spot"] ?? 0),
    noFlip: Number(row?.["no_flip"] ?? 0),
    reasons,
    totalFailure: ticks > 0 && refused === ticks,
  };
}

/** Mirrors `analytics.mark_coverage()` for one session. */
function readMarkCoverage(db: DatabaseHandle, session: string | null): BwbPayload["integrity"]["markCoverage"] {
  if (session === null) return { session: null, marks: 0, refused: 0, refusalShare: null };
  const row = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM bwb_marks WHERE session_date = ?",
    )
    .get(session);
  const marks = Number(row?.["total"] ?? 0);
  const refused = Number(row?.["refused"] ?? 0);
  return { session, marks, refused, refusalShare: marks > 0 ? refused / marks : null };
}

/** Today's entry attempts, refusals included -- `bwb_entry_attempts`. */
function readEntryAttemptsToday(db: DatabaseHandle, session: string | null): BwbEntryAttempt[] {
  if (session === null) return [];
  return db
    .prepare<[string], Record<string, unknown>>(
      "SELECT ts, symbol, book, outcome, credit FROM bwb_entry_attempts WHERE trade_date = ? ORDER BY ts",
    )
    .all(session)
    .map((r) => ({
      ts: str(r["ts"]) ?? "",
      symbol: str(r["symbol"]) ?? "",
      book: str(r["book"]) ?? "",
      outcome: str(r["outcome"]) ?? "",
      credit: num(r["credit"]),
    }));
}

/** Today's management verdicts -- `bwb_management_events`. */
function readManagementEventsToday(db: DatabaseHandle, session: string | null): BwbManagementEvent[] {
  if (session === null) return [];
  return db
    .prepare<[string], Record<string, unknown>>(
      `SELECT position_id, occurred_at, action, reason, executed, gate FROM bwb_management_events
        WHERE session_date = ? ORDER BY occurred_at`,
    )
    .all(session)
    .map((r) => ({
      positionId: str(r["position_id"]) ?? "",
      occurredAt: num(r["occurred_at"]) ?? 0,
      action: str(r["action"]) ?? "",
      reason: str(r["reason"]) ?? "",
      executed: r["executed"] === 1,
      gate: str(r["gate"]),
    }));
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

export function readBwb(config: ConsoleConfig): BwbPayload {
  const empty: BwbPayload = {
    session: null,
    dbPresent: false,
    openPositions: [],
    openCount: 0,
    books: [],
    fireCounts: [],
    correlationCaveat: CORRELATION_CAVEAT,
    entryAttemptsToday: [],
    managementEventsToday: [],
    integrity: {
      triggerCoverage: EMPTY_TRIGGER_COVERAGE,
      markCoverage: { session: null, marks: 0, refused: 0, refusalShare: null },
      schemaDrift: [],
      measurementBreaks: [],
    },
    today: { lastIteration: null },
  };

  return withReadOnlyDb<BwbPayload>(dbPath(config), empty, (db) => {
    const session = latestSession(db);
    const openPositions = readOpenPositions(db);

    const iteration = db
      .prepare<[], Record<string, unknown>>(
        "SELECT ran_at, phase, status FROM bwb_loop_iterations ORDER BY ran_at DESC LIMIT 1",
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
      fireCounts: readFireCounts(db),
      correlationCaveat: CORRELATION_CAVEAT,
      entryAttemptsToday: readEntryAttemptsToday(db, session),
      managementEventsToday: readManagementEventsToday(db, session),
      integrity: {
        triggerCoverage: readTriggerCoverage(db, session),
        markCoverage: readMarkCoverage(db, session),
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
    };
  });
}

export interface BwbHistoryFilter {
  book: string | null;
  symbol: string | null;
}

/** Completed positions, newest first. */
export function readBwbHistory(
  config: ConsoleConfig,
  filter: BwbHistoryFilter,
  page: PageRequest = FIRST_PAGE,
): Paged<BwbCycleRow> {
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

  return withReadOnlyDb<Paged<BwbCycleRow>>(dbPath(config), emptyPage(page), (db) =>
    pagedQuery<BwbCycleRow>(
      db,
      {
        columns: `position_id, symbol, book, entry_session, closed_session, status, exit_reason,
                  body_strike, near_strike, far_strike, expiration, entry_spot, entry_credit,
                  armed_at, addon_fired_at, addon_credit, gross_pnl, fees`,
        from: "bwb_positions",
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
          bodyStrike: num(r["body_strike"]),
          nearStrike: num(r["near_strike"]),
          farStrike: num(r["far_strike"]),
          expiration: str(r["expiration"]),
          entrySpot: num(r["entry_spot"]),
          entryCredit: num(r["entry_credit"]),
          armedAt: str(r["armed_at"]),
          addonFiredAt: str(r["addon_fired_at"]),
          addonCredit: num(r["addon_credit"]),
          grossPnl: gross,
          fees,
          netPnl: gross === null || fees === null ? null : gross - fees,
        };
      },
    ),
  );
}

/** The history filter's own options. No era mechanism: the module has one era and no pooled data yet. */
export function readBwbMeta(config: ConsoleConfig): BwbMeta {
  const empty: BwbMeta = { books: [], symbols: [], sessions: [] };
  return withReadOnlyDb<BwbMeta>(dbPath(config), empty, (db) => {
    const column = (name: string, table: string): string[] =>
      db
        .prepare<[], Record<string, unknown>>(`SELECT DISTINCT ${name} AS v FROM ${table} ORDER BY ${name}`)
        .all()
        .map((r) => str(r["v"]) ?? "")
        .filter((v) => v !== "");
    return {
      books: column("book", "bwb_positions"),
      symbols: column("symbol", "bwb_positions"),
      sessions: column("entry_session", "bwb_positions").reverse(),
    };
  });
}
