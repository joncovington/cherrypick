import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, str, numLoose } from "./db.js";
import { resolveCurveSession } from "./curve.js";
import { resolvePmccSession } from "./pmcc.js";
import { resolveBwbSession } from "./bwb.js";

/**
 * The collapsed decision journal (`core.ledgerstore.record_decision`'s own table) -- one row per
 * distinct (date, book, symbol, mode, reason), with `occurrences` counting a gate that refused the
 * same way tick after tick as ONE row rather than flooding the table. curve/pmcc/bwb all write it
 * (`paper_loop.py`'s own `db.record_decision(...)` calls) and none of the three had a console
 * reader for it before this -- the data existed, nothing read it. flies/calendars already have
 * narrow, single-purpose reads of their own `*_decisions` table (a cadence-reason lookup, a
 * week's top skip-reason); this is deliberately not a fourth copy of that -- it is the first
 * general "every decision this session" card, so curve/pmcc/bwb get one rather than each growing
 * their own bespoke slice of the same table.
 *
 * meic has no equivalent: it never calls `record_decision` at all, so there is nothing this reader
 * could show for it without a change to the module's own engine first.
 */

export type DecisionsModule = "curve" | "pmcc" | "bwb";

export interface DecisionRow {
  book: string;
  symbol: string;
  reason: string;
  accepted: boolean;
  occurrences: number;
  detail: string | null;
}

export interface DecisionsPayload {
  module: DecisionsModule;
  tradeDate: string | null;
  rows: DecisionRow[];
}

interface Spec {
  dir: (config: ConsoleConfig) => string;
  table: string;
  resolveSession: (config: ConsoleConfig) => string | null;
}

const SPECS: Record<DecisionsModule, Spec> = {
  curve: { dir: (c) => c.paths.curveDir, table: "curve_decisions", resolveSession: resolveCurveSession },
  pmcc: { dir: (c) => c.paths.pmccDir, table: "pmcc_decisions", resolveSession: resolvePmccSession },
  bwb: { dir: (c) => c.paths.bwbDir, table: "bwb_decisions", resolveSession: resolveBwbSession },
};

/**
 * `date` explicit wins; otherwise the module's own resolved session (the loop's last RUN, not this
 * table's own last row) -- the same rule `resolvePmccSession` exists to enforce, so this reader
 * cannot reintroduce the "two cards, two dates" incident for a fourth table.
 */
export function readDecisions(config: ConsoleConfig, module: DecisionsModule, date: string | null): DecisionsPayload {
  const spec = SPECS[module];
  const dbPath = path.join(spec.dir(config), "paper_trades.db");
  const empty: DecisionsPayload = { module, tradeDate: null, rows: [] };
  const tradeDate = date ?? spec.resolveSession(config);
  if (tradeDate === null) return empty;

  return withReadOnlyDb<DecisionsPayload>(dbPath, empty, (db) => {
    const rows = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT book, symbol, reason, accepted, occurrences, detail
           FROM ${spec.table}
          WHERE trade_date = ?
          ORDER BY accepted ASC, occurrences DESC, book ASC`,
      )
      .all(tradeDate)
      .map((r) => ({
        book: str(r["book"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        reason: str(r["reason"]) ?? "",
        accepted: Number(r["accepted"] ?? 0) === 1,
        occurrences: Math.round(numLoose(r["occurrences"]) ?? 1),
        detail: str(r["detail"]),
      }));
    return { module, tradeDate, rows };
  });
}
