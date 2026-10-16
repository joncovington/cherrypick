import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterAll, describe, expect, it } from "vitest";
import { closePooledDbs, withReadOnlyDb, withReadSnapshot } from "../src/readers/db.js";

/**
 * A reader that totals in one query and breaks down in another must see one ledger for both. The
 * MEIC summary did not: its hero and its arm rows straddled the loop's write mid-session, and the
 * hero stopped equalling the sum of the arms until the next poll (2026-10-16). Here a module's
 * write lands between a reader's two statements, as it does in production.
 */
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "read-snapshot-"));
const file = path.join(dir, "ledger.db");
const writer = new Database(file);
writer.pragma("journal_mode = WAL");
writer.exec("CREATE TABLE trades (net REAL)");
writer.prepare("INSERT INTO trades VALUES (?)").run(10);

afterAll(() => {
  closePooledDbs();
  writer.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

function totalThenRows(read: typeof withReadOnlyDb): { total: number; rows: number } {
  return read(file, { total: -1, rows: -1 }, (db) => {
    const total = db.prepare("SELECT SUM(net) AS s FROM trades").get() as { s: number };
    // The module records a trade between the reader's two statements.
    writer.prepare("INSERT INTO trades VALUES (?)").run(5);
    const rows = (db.prepare("SELECT net FROM trades").all() as Array<{ net: number }>).reduce((s, r) => s + r.net, 0);
    return { total: total.s, rows };
  });
}

describe("withReadSnapshot", () => {
  it("statement by statement, the total and its rows can disagree", () => {
    const r = totalThenRows(withReadOnlyDb);
    expect(r.rows).not.toBe(r.total);
  });

  it("in one snapshot, they always agree", () => {
    const r = totalThenRows(withReadSnapshot);
    expect(r.rows).toBe(r.total);
  });
});
