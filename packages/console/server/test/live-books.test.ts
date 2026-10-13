import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readLiveBooks } from "../src/readers/liveBooks.js";

/**
 * The Overview's live panel lists every module with a live book. Mirrors of each module's own
 * settled-net rule (flies: `gross_pnl - fees` over settled rows by `trade_date`; bwb: over closed
 * rows by `closed_session`); a cancelled entry is not activity; an absent ledger and a failed read
 * are different answers.
 */

function home(): { cfg: ConsoleConfig; flies: string; bwb: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "live-books-"));
  const flies = path.join(root, "flies");
  const bwb = path.join(root, "bwb");
  fs.mkdirSync(flies);
  fs.mkdirSync(bwb);
  return { cfg: { paths: { fliesDir: flies, bwbDir: bwb } } as unknown as ConsoleConfig, flies, bwb };
}

describe("readLiveBooks", () => {
  it("reads each live ledger by its module's own rule", () => {
    const { cfg, flies, bwb } = home();
    const f = new Database(path.join(flies, "live_trades.db"));
    f.exec("CREATE TABLE fly_positions (trade_date TEXT, status TEXT, gross_pnl REAL, fees REAL)");
    const ins = f.prepare("INSERT INTO fly_positions VALUES (?, ?, ?, ?)");
    ins.run("2026-10-09", "settled", 100, 10);
    ins.run("2026-10-12", "settled", 50, 5.5);
    ins.run("2026-10-12", "settled", -200, 7);
    ins.run("2026-10-13", "open", null, 3);
    ins.run("2026-10-13", "cancelled", null, 0);
    f.close();
    const b = new Database(path.join(bwb, "live_trades.db"));
    b.exec("CREATE TABLE bwb_positions (entry_session TEXT, closed_session TEXT, status TEXT, gross_pnl REAL, fees REAL)");
    b.prepare("INSERT INTO bwb_positions VALUES (?, ?, ?, ?, ?)").run("2026-10-13", null, "cancelled", null, null);
    b.close();

    const { books } = readLiveBooks(cfg);
    const fl = books.find((x) => x.module === "flies")!;
    expect(fl.ledger).toBe("ok");
    expect(fl.positions).toBe(4);
    expect(fl.open).toBe(1);
    expect(fl.latestSession).toBe("2026-10-13");
    expect(fl.latestNetSession).toBe("2026-10-12");
    expect(fl.latestNet).toBe(-162.5);
    expect(fl.totalNet).toBe(-72.5);
    const bw = books.find((x) => x.module === "bwb")!;
    expect(bw.ledger).toBe("ok");
    expect(bw.positions).toBe(0); // a cancelled entry never held risk
  });

  it("tells an absent ledger from a failed read", () => {
    const { cfg, bwb } = home();
    fs.writeFileSync(path.join(bwb, "live_trades.db"), "not a database");
    const { books } = readLiveBooks(cfg);
    expect(books.find((x) => x.module === "flies")?.ledger).toBe("absent");
    const bw = books.find((x) => x.module === "bwb")!;
    expect(bw.ledger).toBe("failed");
    expect(bw.error).not.toBeNull();
  });
});
