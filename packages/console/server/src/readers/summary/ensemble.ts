import path from "node:path";
import type { EnsembleHistoryRow, ModuleSummarySlice } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { NO_RANGE } from "../dateRange.js";
import { hasTable, withReadOnlyDb } from "../db.js";
import { readEnsembleHistory } from "../ensemble.js";
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
 * ensemble's simplified-view summary. A trade is one structure an arm entered once in a session and
 * closed by the arm's own exit or held to the posted close. The money is `readEnsembleHistory`'s own
 * rows (entry + exit = gross; gross - fees - settlement - slippage = net), booked to the session the
 * structure ended. Paper only, so `q.mode` is not read. Arms are whatever the ledger names.
 */

const UNIT = "one structure an arm entered in a session, closed by its exit or held to the posted close";

interface StructureTrade extends SummaryTrade {
  symbol: string;
  exitReason: string | null;
}

function toTrade(r: EnsembleHistoryRow): StructureTrade {
  return {
    session: (r.exitSession ?? r.expiration).slice(0, 10),
    arm: r.arm,
    symbol: r.symbol,
    gross: r.grossPnl ?? 0,
    fees: r.fees,
    settlement: r.settlementFees,
    slippage: r.slippage,
    net: r.netPnl ?? 0,
    entry: r.entrySession,
    exitReason: r.exitReason,
  };
}

const END_LABEL: Record<string, string> = {
  touch: "spot touch",
  profit_target: "profit target",
  time: "time exit",
  settlement: "held to the posted close",
};
const END_ORDER = ["spot touch", "profit target", "time exit", "held to the posted close"] as const;

export function readEnsembleSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const dbFile = path.join(config.paths.ensembleDir, "paper_trades.db");
  const openEntries = withReadOnlyDb<string[] | null>(dbFile, null, (db) => {
    if (!hasTable(db, "ensemble_positions")) return null;
    const scope = scopeClauses(q, { arm: "arm", symbol: "symbol" });
    return db
      .prepare<string[], { s: string | null }>(`SELECT entry_session AS s FROM ensemble_positions WHERE status = 'open'${scope.sql}`)
      .all(...scope.params)
      .map((r) => (r.s ?? "").slice(0, 10));
  });

  if (openEntries === null) {
    const slice = emptySlice("ensemble", "paper", UNIT, summaryWindow(q.range, q.day));
    slice.notes.push("No ensemble ledger was found, so there is nothing to summarise yet.");
    return slice;
  }

  const all = scopeTrades(readEnsembleHistory(config, { arm: null, range: NO_RANGE }).rows.map(toTrade), q);
  const session = sessionNeighbours(
    all.map((t) => t.session),
    q.day,
  );
  const window = summaryWindow(q.range, session.day);
  const trades = all.filter((t) => inWindow(t.session, window));
  const open = openEntries.filter((s) => window.to === null || s <= window.to).length;

  return {
    module: "ensemble",
    mode: "paper",
    window,
    session,
    unit: UNIT,
    hero: heroOf(trades, true, open),
    tiles: [],
    gauges: [],
    breakdowns: [
      breakdownOf("by underlying", "symbol", trades, (t) => t.symbol || null),
      breakdownOf("by how it ended", "exit", trades, (t) => (t.exitReason === null ? null : (END_LABEL[t.exitReason] ?? t.exitReason)), END_ORDER),
    ],
    arms: armsOf(trades),
    notes: [
      "A trade here is one structure an arm entered: its legs are placed by the arm's own rule and it is sized as the arm sizes it.",
      "A structure is booked to the session it ended. Gross is the cash at the mid in and out; fees, settlement and slippage are each charged as their own cost.",
      "Structures still open are counted, but their money is not in these figures until they close.",
      "Paper only: this module has no live path.",
    ],
  };
}
