import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, hasTable, hasColumn, str, numLoose } from "./db.js";
import type { PerformanceModuleId } from "./performance.js";

/**
 * Realized exit reasons per profile/book, plus what an execution gate held back before a verdict
 * could act -- read directly against each module's own ledger (a query, not a derivation, so this
 * reads straight off the tables like `readers/attempts.ts`, never through a Python bridge).
 *
 * Two queries, same SPEC table shape as `attempts.ts`'s: closed positions grouped by
 * (tag, exit_reason) for what actually happened, and `*_management_events WHERE executed = 0`
 * grouped by (tag, action, reason, gate) for what a gate refused to act on -- the only record that
 * an exit was SEEN before it was allowed to be taken (`packages/earnings`' own management_events
 * docstring). The five modules with a management-events table (earnings/calendars/pmcc/curve/bwb)
 * share byte-identical `action`/`reason`/`executed`/`gate` columns, verified before writing one
 * query for all five (this repo's own "normalise the identifier, then compare" dedup rule) -- they
 * differ only in table name and the join key back to the position (`order_id` for earnings,
 * `position_id` for the other four).
 *
 * MEIC carries `exit_reason` but keeps no management-events table (no held-back verdicts are
 * recorded there), so its `heldBack` is always empty rather than unavailable -- that is a real
 * "nothing was held back to show," not a missing table. Flies carries neither an `exit_reason`
 * column nor a management-events table (0DTE legs settle or stop; there is no single exit-reason
 * concept the way a multi-day structure has one), so the whole module reports `{unavailable}`
 * rather than a misleadingly empty table.
 */

export type ExitReasonsModule = PerformanceModuleId;

export interface ExitReasonRow {
  tag: string;
  reason: string;
  n: number;
  net: number | null;
  avgNet: number | null;
}

export interface HeldBackRow {
  tag: string;
  action: string;
  reason: string;
  gate: string | null;
  n: number;
}

export interface ExitReasonsResult {
  exitReasons: ExitReasonRow[] | { unavailable: string };
  heldBack: HeldBackRow[];
}

interface Spec {
  dir: (config: ConsoleConfig) => string;
  positionsTable: string;
  tagColumn: string;
  closedWhere: string;
  netExpr: string;
  events: { table: string; positionKey: string; eventKey: string } | null;
}

// Flies is deliberately absent: it carries neither an `exit_reason` column nor a management-events
// table (0DTE legs settle or stop; there is no single exit-reason concept a multi-day structure
// has), so it has no SPEC to run a query against rather than a spec that would run one and find
// nothing.
const SPECS: Partial<Record<ExitReasonsModule, Spec>> = {
  meic: {
    dir: (config) => config.paths.meicDir,
    positionsTable: "ic_trades",
    tagColumn: "risk_profile",
    closedWhere: "exit_time IS NOT NULL",
    netExpr: "pnl - fees",
    events: null,
  },
  earnings: {
    dir: (config) => config.paths.earningsDir,
    positionsTable: "trades",
    tagColumn: "profile",
    closedWhere: "closed_at IS NOT NULL",
    netExpr: "pnl - entry_cost - exit_cost",
    events: { table: "management_events", positionKey: "order_id", eventKey: "order_id" },
  },
  calendars: {
    dir: (config) => config.paths.calendarsDir,
    positionsTable: "dc_positions",
    tagColumn: "book",
    closedWhere: "status = 'closed'",
    netExpr: "gross_pnl - fees",
    events: { table: "dc_management_events", positionKey: "position_id", eventKey: "position_id" },
  },
  pmcc: {
    dir: (config) => config.paths.pmccDir,
    positionsTable: "pmcc_positions",
    tagColumn: "book",
    closedWhere: "status = 'closed'",
    netExpr: "gross_pnl - fees",
    events: { table: "pmcc_management_events", positionKey: "position_id", eventKey: "position_id" },
  },
  curve: {
    dir: (config) => config.paths.curveDir,
    positionsTable: "curve_positions",
    tagColumn: "book",
    closedWhere: "status = 'closed'",
    netExpr: "gross_pnl - fees",
    events: { table: "curve_management_events", positionKey: "position_id", eventKey: "position_id" },
  },
  bwb: {
    dir: (config) => config.paths.bwbDir,
    positionsTable: "bwb_positions",
    tagColumn: "book",
    closedWhere: "status = 'closed'",
    netExpr: "gross_pnl - fees",
    events: { table: "bwb_management_events", positionKey: "position_id", eventKey: "position_id" },
  },
};

const NO_CONCEPT = "flies has no exit_reason column and no management-events table — 0DTE legs settle or stop, with no single exit-reason concept a multi-day structure has";
const NO_LEDGER = (module: string) => `${module} exit reasons unavailable — its paper ledger has no readable position table yet`;

export function readExitReasons(config: ConsoleConfig, module: ExitReasonsModule): ExitReasonsResult {
  const spec = SPECS[module];
  // Flies genuinely has no concept to read, not merely an absent/unreadable ledger -- a distinct
  // message from the fallback below, so "this module doesn't have exit reasons" and "this
  // module's ledger isn't there yet" don't look like the same finding.
  if (spec === undefined) return { exitReasons: { unavailable: NO_CONCEPT }, heldBack: [] };

  const empty: ExitReasonsResult = { exitReasons: { unavailable: NO_LEDGER(module) }, heldBack: [] };
  const dbPath = path.join(spec.dir(config), "paper_trades.db");

  return withReadOnlyDb<ExitReasonsResult>(dbPath, empty, (db) => {
    if (!hasTable(db, spec.positionsTable) || !hasColumn(db, spec.positionsTable, "exit_reason")) {
      return empty;
    }

    const reasonRows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT ${spec.tagColumn} AS tag, exit_reason AS reason, COUNT(*) AS n,
                SUM(${spec.netExpr}) AS net, AVG(${spec.netExpr}) AS avg_net
           FROM ${spec.positionsTable}
          WHERE ${spec.closedWhere} AND exit_reason IS NOT NULL
          GROUP BY tag, reason
          ORDER BY tag, reason`,
      )
      .all();
    const exitReasons: ExitReasonRow[] = reasonRows.map((r) => ({
      tag: str(r["tag"]) ?? "",
      reason: str(r["reason"]) ?? "",
      n: numLoose(r["n"]) ?? 0,
      net: numLoose(r["net"]),
      avgNet: numLoose(r["avg_net"]),
    }));

    let heldBack: HeldBackRow[] = [];
    if (spec.events !== null && hasTable(db, spec.events.table) && hasColumn(db, spec.events.table, "executed")) {
      const ev = spec.events;
      const heldRows = db
        .prepare<[], Record<string, unknown>>(
          `SELECT p.${spec.tagColumn} AS tag, e.action AS action, e.reason AS reason, e.gate AS gate,
                  COUNT(*) AS n
             FROM ${ev.table} e
             JOIN ${spec.positionsTable} p ON e.${ev.eventKey} = p.${ev.positionKey}
            WHERE e.executed = 0
            GROUP BY tag, action, reason, gate
            ORDER BY tag, action, reason`,
        )
        .all();
      heldBack = heldRows.map((r) => ({
        tag: str(r["tag"]) ?? "",
        action: str(r["action"]) ?? "",
        reason: str(r["reason"]) ?? "",
        gate: str(r["gate"]),
        n: numLoose(r["n"]) ?? 0,
      }));
    }

    return { exitReasons, heldBack };
  });
}
