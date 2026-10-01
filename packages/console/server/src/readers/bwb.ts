import { rangeClauses, type DateRange } from "./dateRange.js";
import path from "node:path";
import type {
  BwbArmCell,
  BwbCycleRow,
  BwbHistory,
  BwbHistoryTotals,
  BwbEntryAttempt,
  BwbFireCount,
  BwbManagementEvent,
  BwbMeta,
  BwbOpenPosition,
  BwbPayload,
  Paged,
  TradingMode,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasColumn, num, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
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
    "id", "position_id", "symbol", "arm", "entry_session", "structure_signature", "quantity",
    "expiration", "body_strike", "near_strike", "far_strike", "entry_time", "entry_spot",
    "entry_atm_strike", "entry_expected_move", "entry_body_mid", "entry_near_mid", "entry_far_mid",
    "entry_credit", "entry_narrow_width", "entry_wide_width", "entry_max_loss", "entry_dte",
    "entry_cost", "entry_slippage", "advice_params", "peak_abs_delta", "below_flip_seen", "armed_at",
    "arm_reason", "addon_fired_at", "addon_short_strike", "addon_long_strike", "addon_credit",
    "addon_cost", "addon_slippage", "status", "exit_reason", "closed_at", "closed_session",
    "settlement_spot", "itm_settlements", "gross_pnl", "fees", "created_at", "updated_at",
    // The advisor stamps and the live scaffold (2026-09-16..18), missing from this list until
    // 2026-09-25 -- so every ledger raised 28 drift warnings for columns the module declares.
    "experiment_id", "advice_base", "entry_order_id", "entry_external_id", "entry_fill_status",
    "entry_limit", "entry_placed_at", "entry_live_floor", "entry_reprice_count", "entry_repriced_at",
    "entry_mid_at_submit", "addon_order_id", "addon_external_id", "addon_fill_status", "addon_placed_at",
    "addon_live_floor", "addon_reprice_count", "addon_repriced_at", "addon_mid_at_submit", "addon_attempts",
    "pending_addon_json", "entry_fee_estimate", "addon_fee_estimate", "fees_source", "modeled_fees",
    "modeled_gross_pnl", "reconciled_at", "settlement_source",
    // The settlement part of `fees` (2026-09-25).
    "settlement_fees",
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

/** The live book is the same schema in its own file (the module's narrow live path, 2026-09). */
const LIVE_DB_FILE = "live_trades.db";

function dbPath(config: ConsoleConfig, mode: TradingMode = "paper"): string {
  return path.join(config.paths.bwbDir, mode === "live" ? LIVE_DB_FILE : DB_FILE);
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
    // A cancelled row is a live entry that never filled: it was never a position (the standard).
    .prepare<[], Record<string, unknown>>(
      "SELECT * FROM bwb_positions WHERE status NOT IN ('closed', 'cancelled') ORDER BY symbol, arm",
    )
    .all()
    .map((p) => {
      const positionId = str(p["position_id"]) ?? "";
      const mark = latestMark.get(positionId);
      return {
        positionId,
        symbol: str(p["symbol"]) ?? "",
        arm: str(p["arm"]) ?? "",
        status: str(p["status"]) ?? "",
        bodyStrike: num(p["body_strike"]),
        nearStrike: num(p["near_strike"]),
        farStrike: num(p["far_strike"]),
        expiration: str(p["expiration"]),
        entrySpot: num(p["entry_spot"]),
        entryCredit: num(p["entry_credit"]),
        entryMaxLoss: num(p["entry_max_loss"]),
        quantity: num(p["quantity"]),
        entryCash: bwbEntryCash(p),
        addOnCash: bwbAddOnCash(p),
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
 * `arm.py` states it: "`gross_pnl` is mid-priced and cost-free (per-leg P&L x100 xqty); `fees` is
 * the TOTAL modeled cost (entry + addon entry + settlement); net is always `gross_pnl - fees`."
 * This mirrors that, substituting the mark-to-market gross for the settled one, so an open row and
 * a closed row mean the same thing by the same arithmetic rather than by two definitions that
 * happen to agree.
 *
 * Gross is `(entry credit + add-on credit + cost to close) x 100 x quantity`. `close_cost` is the
 * SIGNED net to unwind EVERY leg at mid, the add-on's included, while `entry_credit` covers only
 * the original fly -- so the add-on credit has to be added back explicitly or a fired position is
 * charged for unwinding legs whose credit was never counted. Caught on the 2026-08-28 cohort, where
 * that omission put the fired `delta` arm at -527.83 beside four identical siblings at -146.89.
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

/** Mirrors `analytics.headline()`'s arm breakdown. */
function readBooks(db: DatabaseHandle): BwbArmCell[] {
  return db
    .prepare<[], Record<string, unknown>>(
      `SELECT arm, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees,
              SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins
         FROM bwb_positions WHERE status = 'closed'
        GROUP BY arm, symbol ORDER BY arm, symbol`,
    )
    .all()
    .map((r) => {
      const n = Number(r["n"] ?? 0);
      const wins = num(r["wins"]);
      return {
        arm: str(r["arm"]) ?? "",
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
      "SELECT arm, COUNT(*) AS n, SUM(addon_fired_at IS NOT NULL) AS fired FROM bwb_positions GROUP BY arm ORDER BY arm",
    )
    .all()
    .map((r) => {
      const n = Number(r["n"] ?? 0);
      const fired = Number(r["fired"] ?? 0);
      return { arm: str(r["arm"]) ?? "", positions: n, fired, fireRate: n > 0 ? fired / n : null };
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
      "SELECT ts, symbol, arm, outcome, credit FROM bwb_entry_attempts WHERE trade_date = ? ORDER BY ts",
    )
    .all(session)
    .map((r) => ({
      ts: str(r["ts"]) ?? "",
      symbol: str(r["symbol"]) ?? "",
      arm: str(r["arm"]) ?? "",
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

export function readBwb(config: ConsoleConfig, mode: TradingMode = "paper"): BwbPayload {
  const empty: BwbPayload = {
    session: null,
    dbPresent: false,
    openPositions: [],
    openCount: 0,
    arms: [],
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

  return withReadOnlyDb<BwbPayload>(dbPath(config, mode), empty, (db) => {
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
      arms: readBooks(db),
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
  arm: string | null;
  symbol: string | null;
  /** Inclusive bounds on the close date; absent means every close. */
  range?: DateRange;
}

/** Completed positions, newest first. */
function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** The opening cash flows, signed: the fly's credit plus the add-on's once it fired, x100 x qty. */
/** The fly's own opening credit, whole position -- the add-on is its own column (2026-09-26). */
function bwbEntryCash(p: Record<string, unknown>): number | null {
  const credit = num(p["entry_credit"]);
  if (credit === null) return null;
  return round2(credit * 100 * (num(p["quantity"]) ?? 1));
}

/**
 * The add-on put credit spread's credit, whole position, once its trigger has fired; null while it
 * has not. A separate entry because it is a separate decision on a later session: `entry + add-on +
 * exit = gross`, and an arm-vs-control reading needs to see what the add-on brought in on its own.
 */
function bwbAddOnCash(p: Record<string, unknown>): number | null {
  const credit = num(p["addon_credit"]);
  if (credit === null) return null;
  return round2(credit * 100 * (num(p["quantity"]) ?? 1));
}

/** A `bwb_positions` column, or NULL on a ledger that predates it. */
function bwbCol(db: DatabaseHandle, name: string): string {
  return hasColumn(db, "bwb_positions", name) ? name : "NULL";
}

/**
 * How a completed position left: settled at the print with something in the money, expired
 * worthless, or closed before expiry.
 */
export function bwbExitKind(exitReason: string | null, itmSettlements: number | null): BwbCycleRow["exitKind"] {
  if (exitReason === null) return null;
  if (exitReason === "expired") return (itmSettlements ?? 0) > 0 ? "settled" : "expired";
  return "closed";
}

/**
 * The standard's money columns for a completed position. `gross_pnl` is mid-priced and cost-free
 * and `fees` the TOTAL (entry fee + entry slippage + add-on fee + add-on slippage + settlement),
 * so the parts come out of it once each and net stays `gross_pnl - fees`. On a live row the
 * slippage is measured against mid and was never charged into `fees`, so none is taken out.
 */
function bwbTradeCash(r: Record<string, unknown>): Pick<
  BwbCycleRow,
  "quantity" | "entryCash" | "addOnCash" | "exitCash" | "exitKind" | "grossPnl" | "fees" | "settlementFees" | "slippage" | "netPnl"
> {
  const gross = num(r["gross_pnl"]);
  const total = num(r["fees"]);
  const settlementFees = num(r["settlement_fees"]);
  // Paper rows leave `fees_source` NULL. Any live row -- modeled, broker estimate or reconciled --
  // records slippage MEASURED against the mid it asked from, and it is never part of `fees` (the
  // live loop's own rule), so there is nothing to take out of the total.
  const live = str(r["fees_source"]) !== null;
  const slipParts = [num(r["entry_slippage"]), num(r["addon_slippage"])];
  const slippage = live || slipParts[0] === null ? null : round2((slipParts[0] ?? 0) + (slipParts[1] ?? 0));
  const entryCash = bwbEntryCash(r);
  const addOnCash = bwbAddOnCash(r);
  return {
    quantity: num(r["quantity"]),
    entryCash,
    addOnCash,
    exitCash: gross !== null && entryCash !== null ? round2(gross - entryCash - (addOnCash ?? 0)) : null,
    exitKind: bwbExitKind(str(r["exit_reason"]), num(r["itm_settlements"])),
    grossPnl: gross,
    fees: total === null ? null : round2(total - (settlementFees ?? 0) - (slippage ?? 0)),
    settlementFees,
    slippage,
    netPnl: gross === null || total === null ? null : round2(gross - total),
  };
}

const EMPTY_BWB_TOTALS: BwbHistoryTotals = { positions: 0, gross: 0, fees: 0, settlementFees: 0, slippage: 0, net: 0 };

export function readBwbHistory(
  config: ConsoleConfig,
  filter: BwbHistoryFilter,
  page: PageRequest = FIRST_PAGE,
): BwbHistory {
  const clauses = ["status = 'closed'"];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  if (filter.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }
  // A result belongs to the session it was realised on, so the range bounds the close.
  const range = rangeClauses("closed_session", filter.range);
  clauses.push(...range.clauses);
  params.push(...range.params);

  return withReadOnlyDb<BwbHistory>(dbPath(config), { ...emptyPage(page), totals: EMPTY_BWB_TOTALS }, (db) => {
    const where = clauses.join(" AND ");
    const cols = `position_id, symbol, arm, entry_session, closed_session, status, exit_reason,
                  body_strike, near_strike, far_strike, expiration, entry_spot, entry_credit,
                  armed_at, addon_fired_at, addon_credit, gross_pnl, fees, quantity, itm_settlements,
                  entry_slippage, addon_slippage,
                  ${bwbCol(db, "addon_short_strike")} AS addon_short_strike,
                  ${bwbCol(db, "addon_long_strike")} AS addon_long_strike,
                  ${bwbCol(db, "settlement_fees")} AS settlement_fees,
                  ${bwbCol(db, "fees_source")} AS fees_source`;
    // Totals from the same rows through the same arithmetic as each row, so the chip and the
    // table cannot disagree about what a column means.
    const totals = db
      .prepare<string[], Record<string, unknown>>(`SELECT ${cols} FROM bwb_positions WHERE ${where}`)
      .all(...params)
      .map(bwbTradeCash)
      .reduce<BwbHistoryTotals>(
        (t, c) => ({
          positions: t.positions + 1,
          gross: round2(t.gross + (c.grossPnl ?? 0)),
          fees: round2(t.fees + (c.fees ?? 0)),
          settlementFees: round2(t.settlementFees + (c.settlementFees ?? 0)),
          slippage: round2(t.slippage + (c.slippage ?? 0)),
          net: round2(t.net + (c.netPnl ?? 0)),
        }),
        EMPTY_BWB_TOTALS,
      );
    const paged = pagedQuery<BwbCycleRow>(
      db,
      {
        columns: cols,
        from: "bwb_positions",
        where,
        params,
        orderBy: "entry_session DESC, id DESC",
      },
      page,
      (r) => {
        return {
          positionId: str(r["position_id"]) ?? "",
          symbol: str(r["symbol"]) ?? "",
          arm: str(r["arm"]) ?? "",
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
          addonShortStrike: num(r["addon_short_strike"]),
          addonLongStrike: num(r["addon_long_strike"]),
          addonCredit: num(r["addon_credit"]),
          ...bwbTradeCash(r),
        };
      },
    );
    return { ...paged, totals };
  });
}

/** The history filter's own options. No era mechanism: the module has one era and no pooled data yet. */
export function readBwbMeta(config: ConsoleConfig): BwbMeta {
  const empty: BwbMeta = { arms: [], symbols: [], sessions: [] };
  return withReadOnlyDb<BwbMeta>(dbPath(config), empty, (db) => {
    const column = (name: string, table: string): string[] =>
      db
        .prepare<[], Record<string, unknown>>(`SELECT DISTINCT ${name} AS v FROM ${table} ORDER BY ${name}`)
        .all()
        .map((r) => str(r["v"]) ?? "")
        .filter((v) => v !== "");
    return {
      arms: column("arm", "bwb_positions"),
      symbols: column("symbol", "bwb_positions"),
      sessions: column("entry_session", "bwb_positions").reverse(),
    };
  });
}
