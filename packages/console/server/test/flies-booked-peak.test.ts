import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { onPeakRiskOver, peakRiskSessions } from "../src/readers/fliesPeakRisk.js";

/**
 * A settled flies book carries flies' own peak (`fly_books.peak_risk`, actual fills), and it is
 * preferred over the live loop's tick record, which can hold an entry's limit price before a
 * better fill is reconciled. The case is 2026-09-25's: recorded 298.44, booked 293.44.
 */

function ledger(withBookPeak: boolean): Database.Database {
  // A file per ledger, never ":memory:": `hasColumn` memoises by file name, and every in-memory
  // database shares one, so a second ledger would be answered with the first one's schema.
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "flies-booked-peak-")), "live_trades.db");
  const db = new Database(file);
  db.exec(
    "CREATE TABLE fly_positions (trade_date TEXT, arm TEXT, gross_pnl REAL, fees REAL, status TEXT, kind TEXT, " +
      "side TEXT, center REAL, wing_width REAL, far_width REAL, net REAL, quantity INTEGER, symbol TEXT, " +
      "entry_time TEXT, completed_at TEXT, entry_mode TEXT, credit REAL, debit REAL, exit_time TEXT, floor_dollars REAL)",
  );
  const pos = db.prepare(
    "INSERT INTO fly_positions VALUES (?, ?, 30, 6.88, 'settled', 'fly', 'put', 6700, 5, NULL, 0.3, 1, 'SPX', " +
      "?, ?, 'legged', 2.2, 1.9, ?, 3.11)",
  );
  pos.run("2026-09-25", "control", "2026-09-25T10:30:47-04:00", "2026-09-25T10:38:49-04:00", "2026-09-25T16:20:31-04:00");
  pos.run("2026-09-26", "control", "2026-09-26T10:30:00-04:00", "2026-09-26T10:40:00-04:00", "2026-09-26T16:20:00-04:00");
  db.exec("CREATE TABLE fly_live_marks (trade_date TEXT, iteration_ts TEXT, open_margin REAL)");
  db.exec("INSERT INTO fly_live_marks VALUES ('2026-09-25', '2026-09-25T10:30:47-04:00', 298.44)");
  db.exec("INSERT INTO fly_live_marks VALUES ('2026-09-26', '2026-09-26T10:30:00-04:00', 310.00)");
  db.exec(
    `CREATE TABLE fly_books (trade_date TEXT, arm TEXT, status TEXT${withBookPeak ? ", peak_risk REAL, peak_risk_at TEXT" : ""})`,
  );
  if (withBookPeak) {
    // 09-25 is booked for control; 09-26 only for another arm, which control must never borrow.
    db.exec("INSERT INTO fly_books VALUES ('2026-09-25', 'control', 'settled', 293.44, '2026-09-25T10:30:47-04:00')");
    db.exec("INSERT INTO fly_books VALUES ('2026-09-26', 'other', 'settled', 999.0, '2026-09-26T10:30:00-04:00')");
  }
  return db;
}

describe("flies peak risk sources", () => {
  it("prefers the settled book's peak to the live loop's tick record", () => {
    const [d26, d25] = peakRiskSessions(ledger(true), { arm: "control" });
    expect(d25).toMatchObject({ session: "2026-09-25", peakRisk: 293.44, peakSource: "booked" });
    // No book of its own arm on 09-26: the tick record, never the other arm's book.
    expect(d26).toMatchObject({ session: "2026-09-26", peakRisk: 310.0, peakSource: "recorded" });
  });

  it("reads the tick record on a ledger whose books predate the peak", () => {
    const [, d25] = peakRiskSessions(ledger(false), { arm: "control" });
    expect(d25).toMatchObject({ peakRisk: 298.44, peakSource: "recorded" });
  });

  it("counts which source each covered session's peak came from", () => {
    const r = onPeakRiskOver(peakRiskSessions(ledger(true), { arm: "control" }));
    expect(r.sessions).toBe(2);
    expect(r.booked).toBe(1);
    expect(r.replayed).toBe(0);
  });
});
