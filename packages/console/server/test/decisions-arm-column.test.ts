import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import Database from "better-sqlite3";
import { readDecisions } from "../src/readers/decisions.js";
import { closePooledDbs } from "../src/readers/db.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * The 2026-09-23 rename turned `book` into `arm` on every `*_decisions` table. This reader still
 * named `book` in its SELECT, the query threw, and `withReadOnlyDb` handed back its empty payload --
 * so curve/pmcc/bwb's decision cards read "nothing refused today" rather than failing. Both
 * spellings are pinned: a pre-migration backup ledger still says `book`.
 */

function ledger(column: "arm" | "book"): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-decisions-"));
  const bwbDir = path.join(home, "bwb");
  fs.mkdirSync(bwbDir, { recursive: true });
  const db = new Database(path.join(bwbDir, "paper_trades.db"));
  db.exec(`CREATE TABLE bwb_decisions (
    id INTEGER PRIMARY KEY, trade_date TEXT, ${column} TEXT, symbol TEXT, mode TEXT, reason TEXT,
    accepted INTEGER, first_seen TEXT, last_seen TEXT, occurrences INTEGER, detail TEXT)`);
  const insert = db.prepare(
    `INSERT INTO bwb_decisions (trade_date, ${column}, symbol, reason, accepted, occurrences, detail)
     VALUES ('2026-09-24', ?, 'SPX', ?, ?, ?, NULL)`,
  );
  insert.run("delta", "entered", 1, 1);
  insert.run("control", "credit_below_floor", 0, 3);
  insert.run("bounce", "credit_below_floor", 0, 9);
  db.close();
  return { port: 0, paths: { bwbDir } } as unknown as ConsoleConfig;
}

afterEach(() => closePooledDbs());

describe("readDecisions after the arm rename", () => {
  for (const column of ["arm", "book"] as const) {
    it(`reads a ledger whose variant column is \`${column}\``, () => {
      const out = readDecisions(ledger(column), "bwb", "2026-09-24");
      // refusals first, most frequent first, then the accepted row
      expect(out.rows.map((r) => [r.book, r.accepted, r.occurrences])).toEqual([
        ["bounce", false, 9],
        ["control", false, 3],
        ["delta", true, 1],
      ]);
    });
  }
});
