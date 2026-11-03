/**
 * The PMCC integrity card's exposure counts are kept as a running count over pmcc's append-only
 * marks: a poll reads only the rows written since the last one. These pin that the running count
 * always equals a fresh count of the whole table -- after appends, and for a ledger rebuilt at the
 * same path, which reuses the same ids and must not inherit the old counts.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readPmcc, resetPmccExposure } from "../src/readers/pmcc.js";
import { closePooledDbs } from "../src/readers/db.js";

const SCHEMA = path.join(__dirname, "fixtures", "pmcc-ledger-schema.sql");

function ledger(dir: string): Database.Database {
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(fs.readFileSync(SCHEMA, "utf-8"));
  return db;
}

function mark(db: Database.Database, positionId: string, exposed: number, usable = 1, shortTv: number | null = 1): void {
  db.prepare(
    `INSERT INTO pmcc_marks (position_id, marked_at, session_date, short_tv, assignment_exposed, usable)
     VALUES (?, 0, '2026-11-02', ?, ?, ?)`,
  ).run(positionId, shortTv, exposed, usable);
}

/** The card's three numbers, counted afresh over the whole table, as `analytics.exposure()` does. */
function fresh(db: Database.Database) {
  const rows = db
    .prepare<[], { exposed: number; marked: number }>(
      `SELECT SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed, COUNT(*) AS marked
         FROM pmcc_marks WHERE short_tv IS NOT NULL AND usable = 1 GROUP BY position_id`,
    )
    .all();
  return {
    positionsWithExposure: rows.filter((r) => r.exposed > 0).length,
    exposedTicks: rows.reduce((s, r) => s + r.exposed, 0),
    markedTicks: rows.reduce((s, r) => s + r.marked, 0),
  };
}

afterEach(() => {
  closePooledDbs();
  resetPmccExposure();
});

describe("PMCC exposure counts", () => {
  it("equal a fresh count after every append, the unusable and unpriced rows left out", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pmcc-exposure-"));
    const db = ledger(dir);
    const base = loadConfig();
    const config = { ...base, paths: { ...base.paths, pmccDir: dir } };
    const card = () => readPmcc(config).integrity.exposure;

    mark(db, "A", 1);
    mark(db, "A", 0);
    mark(db, "B", 0);
    expect(card()).toEqual(fresh(db));

    mark(db, "B", 1);
    mark(db, "B", 1, 0); // unusable
    mark(db, "C", 1, 1, null); // no short time value
    mark(db, "C", 0);
    expect(card()).toEqual(fresh(db));
    expect(card()).toEqual({ positionsWithExposure: 2, exposedTicks: 2, markedTicks: 5 });
    db.close();
  });

  it("starts again for a ledger rebuilt at the same path", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pmcc-exposure-"));
    const base = loadConfig();
    const config = { ...base, paths: { ...base.paths, pmccDir: dir } };
    const first = ledger(dir);
    for (let i = 0; i < 4; i += 1) mark(first, "OLD", 1);
    expect(readPmcc(config).integrity.exposure.exposedTicks).toBe(4);
    first.close();
    closePooledDbs();

    fs.rmSync(path.join(dir, "paper_trades.db"));
    for (const ext of ["-wal", "-shm"]) fs.rmSync(path.join(dir, `paper_trades.db${ext}`), { force: true });
    const rebuilt = ledger(dir);
    for (let i = 0; i < 6; i += 1) mark(rebuilt, "NEW", 0);
    expect(readPmcc(config).integrity.exposure).toEqual(fresh(rebuilt));
    expect(readPmcc(config).integrity.exposure).toEqual({ positionsWithExposure: 0, exposedTicks: 0, markedTicks: 6 });
    rebuilt.close();
  });
});
