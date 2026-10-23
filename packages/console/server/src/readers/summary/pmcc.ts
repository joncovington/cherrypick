import type { ModuleSummarySlice, SummaryGauge, SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { num, str, withReadSnapshot, type DatabaseHandle } from "../db.js";
import { pmccDbPath } from "../pmcc.js";
import { positionCash, positionCashColumns, positionCol } from "../positionCash.js";
import { unrealisedByPosition } from "../unrealised.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  worstNet,
  inWindow,
  scopeClauses,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * PMCC's simplified-view summary.
 *
 * A "trade" here is what pmcc's history table lists as one row: a POSITION -- one long call and every
 * short call sold against it -- counted on the session it closed (`closed_session`, the bound
 * `readPmccHistory` filters on). Its money is the history reader's own: the same columns through
 * the same `positionCash`, so the hero equals the history totals for the same window. A position
 * whose short settled in the money and whose delivered shares await cover (`short_settled`) is not
 * final, and history does not sum it; nor does this -- it counts as open.
 *
 * Paper only, structurally (no live loop, no live ledger), so `q.mode` is not honoured.
 *
 * The held-long arms (`shield`, `shield_hold`) close about ten months after entry, so for most of a
 * year their closed result is absent here; what IS current about them -- shorts sold and rolled,
 * the assignment-exposure flags, the open book's mark -- is read from the legs, the management
 * events and the marks, each bounded by the same window.
 */

const UNIT = "one PMCC position: a long call and every short call sold against it, counted when it closes";

/** The held-long entry band's low edge (`clock.leap_expiration`), as the pmcc page reads it. */
const HELD_LONG_MIN_DTE = 240;

const EXIT_KIND_ORDER = ["closed", "expired", "settled", "assigned"] as const;

interface PmccTrade extends SummaryTrade {
  symbol: string;
  exitKind: string | null;
  lifecycle: "held-long" | "weekly" | null;
  /** The long call's entry mid x 100 x quantity (the tracker's `long_cost`); null = not recorded. */
  longCost: number | null;
}

interface Activity {
  open: number;
  openNet: number | null;
  openPriced: number;
  shortsSold: number;
  shortsUndated: number;
  rolls: number;
  exposedMarks: number;
  shortMarks: number;
  exposedPositions: number;
}

type Window = { from: string | null; to: string | null; label: string };

const round2 = (v: number) => Math.round(v * 100) / 100;

/** A session-bounded SQL clause on `col` for the window (open ends drop out). */
function windowSql(col: string, w: Window): { sql: string; params: string[] } {
  const parts: string[] = [];
  const params: string[] = [];
  if (w.from !== null) {
    parts.push(`${col} >= ?`);
    params.push(w.from);
  }
  if (w.to !== null) {
    parts.push(`${col} <= ?`);
    params.push(w.to);
  }
  return { sql: parts.length > 0 ? ` AND ${parts.join(" AND ")}` : "", params };
}

/** Every closed position, through the history reader's own columns and `positionCash`. */
function readClosed(db: DatabaseHandle): { trades: PmccTrade[]; unpriced: number } {
  const rows = db
    .prepare<[], Record<string, unknown>>(
      `SELECT position_id, symbol, arm, closed_session, net_debit,
              ${positionCol(db, "pmcc_positions", "entry_long_dte")} AS entry_long_dte,
              (SELECT l.entry_mid FROM pmcc_legs l
                WHERE l.position_id = pmcc_positions.position_id AND l.leg_role = 'long_call') AS long_mid,
              ${positionCashColumns(db, "pmcc_")}
         FROM pmcc_positions
        WHERE status = 'closed' AND closed_session IS NOT NULL`,
    )
    .all();
  const trades: PmccTrade[] = [];
  let unpriced = 0;
  for (const r of rows) {
    // net_debit is a debit, positive: history signs it as money paid (readPmccHistory's entryOf).
    const entry = num(r["net_debit"]) === null ? null : -Number(r["net_debit"]);
    const m = positionCash(r, entry);
    if (m.grossPnl === null || m.netPnl === null) {
      unpriced += 1;
      continue;
    }
    const dte = num(r["entry_long_dte"]);
    const longMid = num(r["long_mid"]);
    trades.push({
      session: str(r["closed_session"]) ?? "",
      arm: str(r["arm"]) ?? "",
      gross: m.grossPnl,
      fees: m.fees,
      settlement: m.settlementFees,
      slippage: m.slippage,
      net: m.netPnl,
      symbol: str(r["symbol"]) ?? "",
      exitKind: m.exitKind,
      lifecycle: dte === null ? null : dte >= HELD_LONG_MIN_DTE ? "held-long" : "weekly",
      longCost: longMid === null ? null : round2(longMid * 100 * (m.quantity ?? 1)),
    });
  }
  return { trades, unpriced };
}

/** What moved in the window besides closes -- shorts sold, rolls, exposure flags -- and the open book now. */
function readActivity(db: DatabaseHandle, w: Window, q: SummaryQuery): Activity {
  const sc = scopeClauses(q, { arm: "arm", symbol: "symbol" });
  // The legs, events and marks tables carry no scope of their own; bound them to the scoped positions.
  const subScope =
    sc.conditions.length > 0
      ? ` AND position_id IN (SELECT position_id FROM pmcc_positions WHERE ${sc.conditions.join(" AND ")})`
      : "";
  const openRows = db
    .prepare<string[], { position_id: string }>(
      `SELECT position_id FROM pmcc_positions WHERE status != 'closed'${sc.sql}`,
    )
    .all(...sc.params);
  const mtm = unrealisedByPosition(db, {
    positionsTable: "pmcc_positions",
    legsTable: "pmcc_legs",
    marksTable: "pmcc_marks",
    assignmentsTable: "pmcc_assignments",
  });
  let openNet = 0;
  let openPriced = 0;
  for (const r of openRows) {
    const v = mtm.get(r.position_id)?.unrealisedNet ?? null;
    if (v === null) continue;
    openNet += v;
    openPriced += 1;
  }

  const legW = windowSql("opened_session", w);
  const legs = db
    .prepare<string[], { n: number | null }>(
      `SELECT COUNT(*) AS n FROM pmcc_legs WHERE leg_role LIKE 'short_call%' AND opened_session IS NOT NULL${legW.sql}${subScope}`,
    )
    .get(...legW.params, ...sc.params);
  const undated = db
    .prepare<string[], { n: number | null }>(
      `SELECT COUNT(*) AS n FROM pmcc_legs WHERE leg_role LIKE 'short_call%' AND opened_session IS NULL${subScope}`,
    )
    .get(...sc.params);

  const evW = windowSql("session_date", w);
  const rolls = db
    .prepare<string[], { n: number | null }>(
      `SELECT COUNT(*) AS n FROM pmcc_management_events WHERE action = 'roll_short' AND executed = 1${evW.sql}${subScope}`,
    )
    .get(...evW.params, ...sc.params);

  // analytics.exposure()'s population: usable marks that carry a short time value.
  const exp = db
    .prepare<string[], { exposed: number | null; marked: number | null; positions: number | null }>(
      `SELECT SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed, COUNT(*) AS marked,
              COUNT(DISTINCT CASE WHEN assignment_exposed = 1 THEN position_id END) AS positions
         FROM pmcc_marks WHERE short_tv IS NOT NULL AND usable = 1${evW.sql}${subScope}`,
    )
    .get(...evW.params, ...sc.params);

  return {
    open: openRows.length,
    openNet: openPriced > 0 ? round2(openNet) : null,
    openPriced,
    shortsSold: Number(legs?.n ?? 0),
    shortsUndated: Number(undated?.n ?? 0),
    rolls: Number(rolls?.n ?? 0),
    exposedMarks: Number(exp?.exposed ?? 0),
    shortMarks: Number(exp?.marked ?? 0),
    exposedPositions: Number(exp?.positions ?? 0),
  };
}

export function readPmccSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const read = withReadSnapshot(pmccDbPath(config), null, (db) => {
    const closed = readClosed(db);
    // The day arrows and the latest-session default follow the scoped closes, not the whole book.
    const session = sessionNeighbours(
      scopeTrades(closed.trades, q).map((t) => t.session),
      q.day,
    );
    const window = summaryWindow(q.range, session.day);
    return { ...closed, session, window, activity: readActivity(db, window, q) };
  });
  if (read === null) {
    const slice = emptySlice("pmcc", "paper", UNIT, summaryWindow(q.range, q.day));
    slice.notes.push("The pmcc paper ledger could not be read, so there is nothing to summarise.");
    return slice;
  }

  const { session, window, activity, unpriced } = read;
  const trades = scopeTrades(read.trades.filter((t) => inWindow(t.session, window)), q);

  const hero = heroOf(trades, true, activity.open);
  const costed = trades.filter((t) => t.longCost !== null);
  const longCost = round2(costed.reduce((s, t) => s + (t.longCost ?? 0), 0));
  const costedNet = round2(costed.reduce((s, t) => s + t.net, 0));
  hero.basis = costed.length > 0 ? { label: "long calls bought", amount: longCost } : null;

  const gauges: SummaryGauge[] = [
    {
      title: "return on long cost",
      rawPct: costed.length > 0 && longCost > 0 ? (costedNet / longCost) * 100 : null,
      min: -20,
      max: 20,
      caption:
        costed.length === trades.length
          ? "net ÷ what the long calls cost at entry, over the positions closed in the window"
          : `net ÷ long cost, over the ${costed.length}/${trades.length} positions whose long-call entry price was recorded`,
      dollars:
        costed.length > 0 && longCost > 0
          ? { amount: costedNet, of: longCost, ofLabel: "long calls bought", worst: worstNet(costed) }
          : null,
    },
  ];
  if (activity.shortMarks > 0) {
    gauges.push({
      title: "marks flagged assignment-exposed",
      rawPct: (activity.exposedMarks / activity.shortMarks) * 100,
      min: 0,
      max: 100,
      caption: `${activity.exposedMarks} of ${activity.shortMarks} usable short-call marks in the window had almost no time value left`,
    });
  }

  const tiles: SummaryTile[] = [
    {
      label: "open now, marked to market",
      value: activity.openNet,
      unit: "usd",
      note:
        activity.open === 0
          ? "no open positions"
          : `net of costs to date, ${activity.openPriced}/${activity.open} open positions priced; not in the net above`,
    },
    {
      label: "short calls sold",
      value: activity.shortsSold,
      unit: "count",
      signed: false,
      note:
        activity.shortsUndated > 0
          ? `opened in the window; ${activity.shortsUndated} older short legs carry no open date and are not counted`
          : "opened in the window, first shorts and roll replacements alike",
    },
    {
      label: "shorts rolled",
      value: activity.rolls,
      unit: "count",
      signed: false,
      note: "short rolls executed in the window (expiry, decay and breach rolls)",
    },
    {
      label: "positions flagged assignment-exposed",
      value: activity.exposedPositions,
      unit: "count",
      signed: false,
      note: "had at least one near-zero-extrinsic short mark in the window; XSP is exempt (European)",
    },
    {
      label: "average net per position",
      value: trades.length > 0 ? round2(hero.net / trades.length) : null,
      unit: "usd",
      note: `over ${trades.length} closed positions`,
    },
  ];

  const breakdowns = [
    breakdownOf("by symbol", "symbol", trades, (t) => t.symbol),
    breakdownOf("by how it ended", "exit", trades, (t) => t.exitKind, EXIT_KIND_ORDER),
    breakdownOf("by long held", "long", trades, (t) => t.lifecycle, ["held-long", "weekly"]),
  ].filter((b) => b.rows.length > 0);

  const retired = readRetiredArms(config).retired.pmcc ?? [];

  const notes: string[] = [
    "Each position is a deep in-the-money long call with short calls sold against it; it is counted on the day it closes. Paper trading only.",
    "Fills are at the mid price; slippage is charged as its own cost beside fees and settlement, so net = gross − fees − settlement − slippage.",
    "Early assignment is not modelled. It is measured instead (the assignment-exposed flags), so the paper result is an upper bound on what a real account would have made.",
    "The main arms (shield, shield_hold, shield_guard) hold a roughly one-year long and close about ten months after entry; until then their result shows only in the 'open now' figure, not in the net above.",
    "Each symbol is its own experiment; read the by-symbol table rather than the pooled total.",
  ];
  if (unpriced > 0) {
    notes.push(`${unpriced} closed position(s) never recorded a gross or fee figure and are left out of every number.`);
  }

  return {
    module: "pmcc",
    mode: "paper",
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns,
    arms: armsOf(trades, retired),
    notes,
  };
}
