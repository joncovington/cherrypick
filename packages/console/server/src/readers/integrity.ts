import type { MeasurementBreak } from "@console/shared";
import type { DatabaseHandle } from "./db.js";

/**
 * Measurement breaks and schema drift, for the modules whose ledger records them.
 *
 * There are two `measurement_breaks` shapes in the suite. The older one (meic, flies) carries
 * `scope/kind/reason/detail`; the LedgerStore one the newer modules use carries
 * `key/old_value/new_value/note`. Both are read here rather than in each module's reader, so a page
 * cannot end up rendering a break the shape of its own schema rather than the shape of the fact.
 *
 * Every read degrades to empty rather than throwing: the console is read-only over every other
 * package's data and must never fail a page over a schema it does not own — the same posture
 * `readEntryAttempts` takes to a ledger written before its table existed.
 */
export function readMeasurementBreaks(db: DatabaseHandle): MeasurementBreak[] {
  for (const q of [
    // Older shape (meic, flies). `scope` is load-bearing: a break can apply to one arm rather
    // than the whole book, and a reader that drops it would over-state what is affected.
    `SELECT break_date AS date, kind AS key, reason AS note, scope FROM measurement_breaks
     ORDER BY break_date DESC, id DESC`,
    // LedgerStore shape (bwb, pmcc, curve, calendars, earnings).
    `SELECT break_date AS date, key AS key, note AS note, NULL AS scope FROM measurement_breaks
     ORDER BY break_date DESC, id DESC`,
  ]) {
    try {
      return db
        .prepare<[], Record<string, unknown>>(q)
        .all()
        .map((r) => ({
          date: String(r["date"] ?? ""),
          key: String(r["key"] ?? ""),
          note: typeof r["note"] === "string" ? r["note"] : null,
          scope: typeof r["scope"] === "string" ? r["scope"] : null,
        }));
    } catch {
      continue; // wrong shape for this ledger -- try the other before giving up
    }
  }
  return [];
}

/** Columns the ledger has that this build does not know, for the tables a caller names. */
export function readSchemaDrift(db: DatabaseHandle, known: Record<string, string[]>): string[] {
  const drift: string[] = [];
  for (const [table, columns] of Object.entries(known)) {
    const knownSet = new Set(columns);
    try {
      for (const col of db.prepare<[], Record<string, unknown>>(`PRAGMA table_info(${table})`).all()) {
        const name = col["name"];
        if (typeof name === "string" && !knownSet.has(name)) drift.push(`${table}.${name}`);
      }
    } catch {
      continue;
    }
  }
  return drift.sort();
}
