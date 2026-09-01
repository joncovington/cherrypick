import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { unrealisedByPosition } from "../src/readers/unrealised.js";

/**
 * The mark-to-market convention pmcc and calendars share, stated in both modules' own `book.py`:
 * gross is the sum of per-leg P&L (`entry - close` for a sold leg, `close - entry` for a bought
 * one) x100 x qty, and net is `gross - fees`. This is that arithmetic with the leg's current usable
 * mark standing in for `close_value`, so an open row and a closed row mean the same thing.
 */

function db(rows: {
  positions: Array<[string, string, number | null, number]>;
  legs: Array<[string, string, string, number | null, string]>;
  marks: Array<[string, string, number | null, number, number]>;
}) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "unreal-")), "l.db");
  const conn = new Database(file);
  conn.exec(`
    CREATE TABLE p (position_id TEXT, status TEXT, fees REAL, quantity INTEGER);
    CREATE TABLE l (position_id TEXT, leg_role TEXT, action TEXT, entry_mid REAL, status TEXT);
    CREATE TABLE m (position_id TEXT, leg_role TEXT, mid REAL, usable INTEGER, marked_at REAL);
  `);
  for (const r of rows.positions) conn.prepare("INSERT INTO p VALUES (?,?,?,?)").run(...r);
  for (const r of rows.legs) conn.prepare("INSERT INTO l VALUES (?,?,?,?,?)").run(...r);
  for (const r of rows.marks) conn.prepare("INSERT INTO m VALUES (?,?,?,?,?)").run(...r);
  return conn;
}

const OPTS = { positionsTable: "p", legsTable: "l", marksTable: "m" };

describe("mark-to-market P&L", () => {
  it("nets a debit spread: bought legs earn close - entry, sold legs earn entry - close", () => {
    // Long 10.00 -> 11.00 (+1.00); short 3.00 -> 2.50 (+0.50). Gross 1.50 x100 = 150, fees 12.
    const conn = db({
      positions: [["A", "open", 12, 1]],
      legs: [
        ["A", "long", "Buy to Open", 10.0, "open"],
        ["A", "short", "Sell to Open", 3.0, "open"],
      ],
      marks: [
        ["A", "long", 11.0, 1, 2],
        ["A", "short", 2.5, 1, 2],
      ],
    });
    const out = unrealisedByPosition(conn as never, OPTS).get("A");
    expect(out?.unrealisedGross).toBe(150);
    expect(out?.unrealisedNet).toBe(138);
    expect(out?.feesToDate).toBe(12);
  });

  it("scales by quantity, because the ledger's own gross does", () => {
    const conn = db({
      positions: [["A", "open", 0, 3]],
      legs: [["A", "long", "Buy to Open", 1.0, "open"]],
      marks: [["A", "long", 1.5, 1, 1]],
    });
    expect(unrealisedByPosition(conn as never, OPTS).get("A")?.unrealisedGross).toBe(150);
  });

  it("uses the LATEST usable mark, not the first or a refused one", () => {
    const conn = db({
      positions: [["A", "open", 0, 1]],
      legs: [["A", "long", "Buy to Open", 1.0, "open"]],
      marks: [
        ["A", "long", 1.2, 1, 1],
        ["A", "long", 9.9, 0, 2], // refused: a recorded row, not a price
        ["A", "long", 1.6, 1, 3],
      ],
    });
    expect(unrealisedByPosition(conn as never, OPTS).get("A")?.unrealisedGross).toBe(60);
  });

  it("refuses a partial mark rather than reporting a P&L for half a position", () => {
    // One leg priced, one not. A partial mark is not a P&L, and a zero standing in for "unknown"
    // is the misleadingly-precise zero this suite's ledgers refuse to write.
    const conn = db({
      positions: [["A", "open", 5, 1]],
      legs: [
        ["A", "long", "Buy to Open", 10.0, "open"],
        ["A", "short", "Sell to Open", 3.0, "open"],
      ],
      marks: [["A", "long", 11.0, 1, 2]],
    });
    const out = unrealisedByPosition(conn as never, OPTS).get("A");
    expect(out?.unrealisedGross).toBeNull();
    expect(out?.unrealisedNet).toBeNull();
    // Fees are still known even when the mark is not, and are still worth showing.
    expect(out?.feesToDate).toBe(5);
  });

  it("leaves net null when fees are unknown, rather than treating them as zero", () => {
    const conn = db({
      positions: [["A", "open", null, 1]],
      legs: [["A", "long", "Buy to Open", 1.0, "open"]],
      marks: [["A", "long", 1.5, 1, 1]],
    });
    const out = unrealisedByPosition(conn as never, OPTS).get("A");
    expect(out?.unrealisedGross).toBe(50);
    expect(out?.unrealisedNet).toBeNull();
  });

  it("ignores closed positions entirely — those carry a realised gross_pnl instead", () => {
    const conn = db({
      positions: [["A", "closed", 1, 1]],
      legs: [["A", "long", "Buy to Open", 1.0, "open"]],
      marks: [["A", "long", 1.5, 1, 1]],
    });
    expect(unrealisedByPosition(conn as never, OPTS).has("A")).toBe(false);
  });
});
