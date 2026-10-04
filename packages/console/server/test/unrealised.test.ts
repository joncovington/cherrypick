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
  legs: Array<[string, string, string, number | null, string] | [string, string, string, number | null, string, number | null]>;
  marks: Array<[string, string, number | null, number, number] | [string, string, number | null, number, number, number | null]>;
  assignments?: Array<[string, string, number, number, string]>;
}) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "unreal-")), "l.db");
  const conn = new Database(file);
  conn.exec(`
    CREATE TABLE p (position_id TEXT, status TEXT, fees REAL, quantity INTEGER);
    CREATE TABLE l (position_id TEXT, leg_role TEXT, action TEXT, entry_mid REAL, status TEXT, close_value REAL);
    CREATE TABLE m (position_id TEXT, leg_role TEXT, mid REAL, usable INTEGER, marked_at REAL, spot REAL);
    CREATE TABLE a (position_id TEXT, direction TEXT, shares INTEGER, basis REAL, status TEXT);
  `);
  for (const r of rows.positions) conn.prepare("INSERT INTO p VALUES (?,?,?,?)").run(...r);
  for (const r of rows.legs) conn.prepare("INSERT INTO l VALUES (?,?,?,?,?,?)").run(...r, ...(r.length === 5 ? [null] : []));
  for (const r of rows.marks) conn.prepare("INSERT INTO m VALUES (?,?,?,?,?,?)").run(...r, ...(r.length === 5 ? [null] : []));
  for (const r of rows.assignments ?? []) conn.prepare("INSERT INTO a VALUES (?,?,?,?,?)").run(...r);
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

  it("counts a settled front short at its settlement value, as finalize will (2026-09-24)", () => {
    // A calendar between Friday's settlement and Monday's disposal: the front short (sold at 2.00)
    // expired worthless, the back long (bought at 3.00) still marks at 2.50. The front credit is
    // real money; reading open legs alone showed -50 here instead of +150.
    const conn = db({
      positions: [["A", "short_settled", 10, 1]],
      legs: [
        ["A", "front", "Sell to Open", 2.0, "settled", 0.0],
        ["A", "back", "Buy to Open", 3.0, "open"],
      ],
      marks: [["A", "back", 2.5, 1, 5, 764.0]],
    });
    const out = unrealisedByPosition(conn as never, OPTS).get("A");
    expect(out?.unrealisedGross).toBe(150);
    expect(out?.unrealisedNet).toBe(140);
  });

  it("prices shares still held from an assignment at the position's latest recorded spot", () => {
    // 2026-09-07's shape: an ITM front short assigned 100 long SPY at 764.20; the position's latest
    // mark read spot 759.43. The front short settled at its intrinsic, 1.20.
    const conn = db({
      positions: [["A", "short_settled", 10, 1]],
      legs: [
        ["A", "front", "Sell to Open", 2.0, "settled", 1.2],
        ["A", "back", "Buy to Open", 3.0, "open"],
      ],
      marks: [["A", "back", 3.0, 1, 5, 759.43]],
      assignments: [["A", "long", 100, 764.2, "open"]],
    });
    const out = unrealisedByPosition(conn as never, { ...OPTS, assignmentsTable: "a" }).get("A");
    // legs: (2.00 - 1.20) + (3.00 - 3.00) = 0.80 -> 80; shares: (759.43 - 764.20) x 100 = -477
    expect(out?.unrealisedGross).toBe(-397);
  });

  it("counts shares already covered on a position still open, at their booked P&L (2026-10-04)", () => {
    // A held-long position outlives its shorts: its assigned short settled at intrinsic, the
    // delivered shares were covered for a booked +35.00, and the long is still open. Until this
    // date only HELD shares were read, so the covered result vanished from the open P&L.
    const conn = db({
      positions: [["A", "open", 5, 1]],
      legs: [
        ["A", "long_call", "Buy to Open", 37.8, "open"],
        ["A", "short_call_1", "Sell to Open", 3.65, "settled", 4.1],
      ],
      marks: [["A", "long_call", 38.2, 1, 5, 72.1]],
    });
    conn.exec("ALTER TABLE a ADD COLUMN share_pnl REAL");
    conn.prepare("INSERT INTO a VALUES (?,?,?,?,?,?)").run("A", "short", 100, 72.1, "disposed", 35);
    const out = unrealisedByPosition(conn as never, { ...OPTS, assignmentsTable: "a" }).get("A");
    // legs: (38.20 - 37.80) + (3.65 - 4.10) = -0.05 -> -5; covered shares +35; gross 30, net 25.
    expect(out?.unrealisedGross).toBe(30);
    expect(out?.unrealisedNet).toBe(25);
  });

  it("reads a covered share position with no booked P&L as unpriced, not zero", () => {
    const conn = db({
      positions: [["A", "open", 5, 1]],
      legs: [["A", "long_call", "Buy to Open", 37.8, "open"]],
      marks: [["A", "long_call", 38.2, 1, 5, 72.1]],
      assignments: [["A", "short", 100, 72.1, "disposed"]],
    });
    expect(unrealisedByPosition(conn as never, { ...OPTS, assignmentsTable: "a" }).get("A")?.unrealisedGross).toBeNull();
  });

  it("refuses held shares it has no spot to price, rather than counting them as zero", () => {
    const conn = db({
      positions: [["A", "open", 0, 1]],
      legs: [["A", "short", "Sell to Open", 1.0, "settled", 2.0]],
      marks: [],
      assignments: [["A", "short", 100, 50.0, "open"]],
    });
    expect(unrealisedByPosition(conn as never, { ...OPTS, assignmentsTable: "a" }).get("A")?.unrealisedGross).toBeNull();
  });
});
