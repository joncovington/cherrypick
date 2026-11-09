import type { ModuleSummarySlice, SummaryTile, WeeklyputHistoryRow } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { NO_RANGE } from "../dateRange.js";
import { hasTable, withReadOnlyDb } from "../db.js";
import { readWeeklyputHistory, weeklyputDbPath } from "../weeklyput.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  inWindow,
  scopeClauses,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * Weekly put's simplified-view summary. A trade is one short index put, sold before a non-monthly
 * weekly expiry and held to it. The money is `readWeeklyputHistory`'s own rows (entry credit +
 * exit intrinsic = gross; gross - fees - settlement - slippage = net), booked to the put's expiry,
 * which is where that history books it. Paper only, so `q.mode` is not read.
 *
 * What the module is judged on, the per-cycle equal-weight mean of net in bp, comes from the
 * module's own `cycles` and `summary` verbs and is not recomputed here; this slide shows the
 * dollars and the per-put shape. It has no gauge: the ledger holds a notional per put, but the
 * history rows this reads do not carry it, and a second arithmetic would be free to drift.
 */

const UNIT = "one short put, sold before a weekly expiry and held to it";

interface PutTrade extends SummaryTrade {
  symbol: string;
  exitKind: WeeklyputHistoryRow["exitKind"];
}

function toTrade(r: WeeklyputHistoryRow): PutTrade {
  return {
    session: r.expiration.slice(0, 10),
    arm: r.arm,
    symbol: r.symbol,
    gross: r.grossPnl ?? 0,
    fees: r.fees,
    settlement: r.settlementFees,
    slippage: r.slippage,
    net: r.netPnl ?? 0,
    entry: r.entrySession,
    exitKind: r.exitKind,
  };
}

function endLabel(kind: WeeklyputHistoryRow["exitKind"]): string {
  switch (kind) {
    case "expired":
      return "expired worthless";
    case "settled":
      return "settled in the money";
    case "assigned":
      return "assigned";
    default:
      return "closed";
  }
}

const END_ORDER = ["expired worthless", "settled in the money", "assigned", "closed"] as const;

export function readWeeklyputSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  // The open puts' entry sessions, from the ledger; null = no ledger or no positions table.
  const openEntries = withReadOnlyDb<string[] | null>(weeklyputDbPath(config), null, (db) => {
    if (!hasTable(db, "weeklyput_positions")) return null;
    const scope = scopeClauses(q, { arm: "arm", symbol: "symbol" });
    return db
      .prepare<string[], { s: string | null }>(`SELECT entry_session AS s FROM weeklyput_positions WHERE status = 'open'${scope.sql}`)
      .all(...scope.params)
      .map((r) => (r.s ?? "").slice(0, 10));
  });

  if (openEntries === null) {
    const slice = emptySlice("weeklyput", "paper", UNIT, summaryWindow(q.range, q.day));
    slice.notes.push("No weekly put ledger was found, so there is nothing to summarise yet.");
    return slice;
  }

  const all = scopeTrades(readWeeklyputHistory(config, { arm: null, range: NO_RANGE }).rows.map(toTrade), q);
  const session = sessionNeighbours(
    all.map((t) => t.session),
    q.day,
  );
  const window = summaryWindow(q.range, session.day);
  const trades = all.filter((t) => inWindow(t.session, window));

  const open = openEntries.filter((s) => window.to === null || s <= window.to).length;
  const hero = heroOf(trades, true, open);

  const tiles: SummaryTile[] = [
    {
      label: "assigned",
      value: trades.length === 0 ? null : trades.filter((t) => t.exitKind === "assigned").length,
      unit: "count",
      note: "puts that ended in shares (physically settled names only)",
      signed: false,
    },
  ];

  return {
    module: "weeklyput",
    mode: "paper",
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges: [],
    breakdowns: [
      breakdownOf("by name", "symbol", trades, (t) => t.symbol || null),
      breakdownOf("by how it ended", "exit", trades, (t) => endLabel(t.exitKind), END_ORDER),
    ],
    arms: armsOf(trades),
    notes: [
      "A trade here is one short put: the index put nearest 10 delta, sold at the mid before each non-monthly weekly expiry and held to it, unhedged.",
      "A put is booked to its expiry session. Gross is the credit less the intrinsic value paid at settlement; fees, settlement and slippage are each charged as their own cost.",
      "Puts still open are counted, but their money is not in these figures until they settle.",
      "Paper only: this module has no live path.",
    ],
  };
}
