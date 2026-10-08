import type { DatabaseHandle } from "./db.js";
import { hasColumn } from "./db.js";

/**
 * A voided session: a day the module struck from the record whole. Read surfaces show nothing of it
 * -- no books, no attempts timeline -- because every position it produced was voided or cancelled,
 * and a timeline of fills that became no position reads as trading that never counted.
 *
 * Both conditions, so neither can hide a real day on its own:
 *   - the module recorded a whole-book (`scope = '*'`) `partial_session` measurement break for the
 *     date -- the 2026-10-08 power outage is the first. An older partial session whose positions
 *     were kept still has held positions and stays visible.
 *   - no position of that day is still held: flies' rows all carry a `void_reason` (or never filled),
 *     MEIC's are all `cancelled` (or never filled). A no-trade day has no break and stays visible.
 *
 * Any missing table or column reads as "not voided": a ledger predating either lane is legitimate.
 */
export type VoidableModule = "flies" | "meic";

const HELD: Record<VoidableModule, { table: string; held: string; columns: string[] }> = {
  flies: {
    table: "fly_positions",
    held: "void_reason IS NULL AND COALESCE(status, '') NOT IN ('cancelled', 'voided')",
    columns: ["trade_date", "void_reason", "status"],
  },
  meic: {
    table: "ic_trades",
    held: "COALESCE(status, '') NOT IN ('cancelled', 'pending', 'partial_entry')",
    columns: ["trade_date", "status"],
  },
};

export function isVoidedSession(db: DatabaseHandle, module: string, day: string): boolean {
  // Only flies and MEIC strike a session today; every other module's sessions are never voided here.
  const spec = (HELD as Record<string, (typeof HELD)[VoidableModule] | undefined>)[module];
  if (spec === undefined) return false;
  if (!spec.columns.every((c) => hasColumn(db, spec.table, c))) return false;
  if (!["break_date", "scope", "kind"].every((c) => hasColumn(db, "measurement_breaks", c))) return false;
  try {
    const brk = db
      .prepare<[string], { n: number }>(
        "SELECT COUNT(*) AS n FROM measurement_breaks WHERE break_date = ? AND scope = '*' AND kind = 'partial_session'",
      )
      .get(day);
    if (!brk || brk.n === 0) return false;
    const held = db
      .prepare<[string], { n: number }>(`SELECT COUNT(*) AS n FROM ${spec.table} WHERE trade_date = ? AND ${spec.held}`)
      .get(day);
    return (held?.n ?? 0) === 0;
  } catch {
    return false;
  }
}

/**
 * Every voided session of a module: the dates with a whole-book `partial_session` break that pass
 * `isVoidedSession`. A list view that spans days (the flies books table) leaves these dates out
 * whole, empty books included -- a struck day shows nothing, not four zero rows.
 */
export function voidedDates(db: DatabaseHandle, module: string): string[] {
  if (!["break_date", "scope", "kind"].every((c) => hasColumn(db, "measurement_breaks", c))) return [];
  try {
    const dates = db
      .prepare<[], { d: string }>(
        "SELECT DISTINCT break_date AS d FROM measurement_breaks WHERE scope = '*' AND kind = 'partial_session'",
      )
      .all()
      .map((r) => r.d);
    return dates.filter((d) => isVoidedSession(db, module, d));
  } catch {
    return [];
  }
}
