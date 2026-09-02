import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { readMeasurementBreaks, readSchemaDrift } from "../src/readers/integrity.js";

/**
 * There are two `measurement_breaks` shapes in the suite -- the older scope/kind/reason one (meic,
 * flies) and the LedgerStore key/note one (bwb, pmcc, curve, calendars, earnings). Both must read,
 * because meic journals five breaks and flies four and NONE of them reached a console page until
 * 2026-08-27: including flies' 2026-08-20 partial session, where a provider bug cost 09:30-10:52 ET
 * and 95 positions, and meic's 2026-08-21 redefinition of what `control` means.
 */

function db(ddl: string, rows: Array<Record<string, unknown>> = [], table = "measurement_breaks") {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "integ-")), "l.db");
  const conn = new Database(file);
  conn.exec(ddl);
  for (const r of rows) {
    const cols = Object.keys(r);
    conn
      .prepare(`INSERT INTO ${table} (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`)
      .run(...cols.map((c) => r[c] as never));
  }
  return conn;
}

const OLD_DDL = `CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT,
  scope TEXT, kind TEXT, reason TEXT, detail TEXT, created_at TEXT);`;
const LEDGERSTORE_DDL = `CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT,
  key TEXT, old_value TEXT, new_value TEXT, note TEXT, recorded_at REAL);`;

describe("reading measurement breaks", () => {
  it("reads the older scope/kind/reason shape (meic, flies)", () => {
    const conn = db(OLD_DDL, [
      { break_date: "2026-08-20", scope: "*", kind: "partial_session", reason: "provider bug" },
      { break_date: "2026-08-11", scope: "control-drift", kind: "arm_added", reason: "ahead of evidence" },
    ]);
    const rows = readMeasurementBreaks(conn as never);
    expect(rows.map((r) => r.key)).toEqual(["partial_session", "arm_added"]);
    // scope is load-bearing: a break can apply to ONE arm rather than the whole book, and a reader
    // that dropped it would over-state what is affected.
    expect(rows[1]?.scope).toBe("control-drift");
    expect(rows[0]?.note).toBe("provider bug");
  });

  it("reads the LedgerStore key/note shape (bwb, pmcc, curve, calendars)", () => {
    const conn = db(LEDGERSTORE_DDL, [
      { break_date: "2026-08-27", key: "trigger_ticks_unmeasured", note: "two defects" },
    ]);
    const rows = readMeasurementBreaks(conn as never);
    expect(rows).toEqual([
      { date: "2026-08-27", key: "trigger_ticks_unmeasured", note: "two defects", scope: null },
    ]);
  });

  it("orders newest first, so the most recent break is the one a reader sees", () => {
    const conn = db(OLD_DDL, [
      { break_date: "2026-08-11", kind: "old", reason: null },
      { break_date: "2026-08-21", kind: "new", reason: null },
    ]);
    expect(readMeasurementBreaks(conn as never).map((r) => r.date)).toEqual(["2026-08-21", "2026-08-11"]);
  });

  it("degrades to empty on a ledger with no such table, rather than failing the page", () => {
    const conn = db("CREATE TABLE unrelated (id INTEGER);");
    expect(readMeasurementBreaks(conn as never)).toEqual([]);
  });
});

describe("schema drift", () => {
  it("names columns the ledger has that this build does not know", () => {
    const conn = db(OLD_DDL);
    const drift = readSchemaDrift(conn as never, {
      measurement_breaks: ["id", "break_date", "scope", "kind", "reason"],
    });
    expect(drift).toEqual(["measurement_breaks.created_at", "measurement_breaks.detail"]);
  });

  it("is empty when the build knows every column", () => {
    const conn = db(OLD_DDL);
    const drift = readSchemaDrift(conn as never, {
      measurement_breaks: ["id", "break_date", "scope", "kind", "reason", "detail", "created_at"],
    });
    expect(drift).toEqual([]);
  });

  it("skips a table that is not there instead of throwing", () => {
    const conn = db(OLD_DDL);
    expect(readSchemaDrift(conn as never, { nope: ["a"] })).toEqual([]);
  });
});
