import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { foldBars, isProfileSymbol, readProfileBars, sessionCount } from "../src/readers/profileBars.js";

/**
 * The Profile tab's reader over `scripts/fetch_profile_bars.py`'s store. The table is created from the
 * script's own SCHEMA text, so a column the recorder renames fails here rather than in the browser.
 * Pinned: regular session only, the newest N sessions oldest first, minutes folded into 5-minute bars,
 * each session's contract, and a missing store said plainly.
 */

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const SCRIPT = fs.readFileSync(path.join(REPO, "scripts", "fetch_profile_bars.py"), "utf-8");
const SCHEMA = /SCHEMA = """([\s\S]*?)"""/.exec(SCRIPT)![1]!;

let tmp: string;
let config: ConsoleConfig;

/** Epoch ms for an ET wall-clock minute in November (EST, UTC-5). */
const ms = (day: string, hh: number, mm: number): number =>
  Date.parse(`${day}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:00-05:00`);

function seed(rows: Array<{ symbol: string; day: string; hh: number; mm: number; px: number; v?: number; contract?: string }>) {
  const dir = path.join(tmp, "data", "market-files");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "profile_bars.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    "INSERT INTO profile_bars (symbol, ts_ms, contract, et_day, et_time, open, high, low, close, volume, fetched_at) " +
      "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
  );
  for (const r of rows) {
    const time = `${String(r.hh).padStart(2, "0")}:${String(r.mm).padStart(2, "0")}`;
    ins.run(r.symbol, ms(r.day, r.hh, r.mm), r.contract ?? r.symbol, r.day, time, r.px, r.px + 1, r.px - 1, r.px + 0.5, r.v ?? 0, 1_763_000_000);
  }
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-profilebars-"));
  config = { paths: { cherrypick: tmp } } as unknown as ConsoleConfig;
});

afterEach(() => {
  closePooledDbs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("profile bars reader", () => {
  it("says plainly when the recorder has not run", () => {
    const p = readProfileBars(config, "SPX", 10);
    expect(p.available).toBe(false);
    expect(p.reason).toMatch(/profile-bars job/);
    expect(p.bars).toEqual([]);
  });

  it("sends the newest sessions' regular hours only, folded into 5-minute bars", () => {
    const rows = [];
    for (const day of ["2026-11-11", "2026-11-12", "2026-11-13"]) {
      rows.push({ symbol: "/ES", day, hh: 4, mm: 0, px: 50, contract: "/ESZ26:XCME" }); // overnight
      for (let m = 0; m < 10; m++) rows.push({ symbol: "/ES", day, hh: 9, mm: 30 + m, px: 100 + m, v: 10, contract: "/ESZ26:XCME" });
      rows.push({ symbol: "/ES", day, hh: 16, mm: 30, px: 500, contract: "/ESZ26:XCME" }); // after the close
    }
    rows.push({ symbol: "SPX", day: "2026-11-13", hh: 9, mm: 30, px: 7000 });
    seed(rows);
    const p = readProfileBars(config, "/ES", 2);
    expect(p.available).toBe(true);
    expect(p.sessions).toEqual(["2026-11-12", "2026-11-13"]);
    expect(p.newestSession).toBe("2026-11-13");
    expect(p.contracts).toEqual({ "2026-11-12": "/ESZ26:XCME", "2026-11-13": "/ESZ26:XCME" });
    expect(p.barSeconds).toBe(300);
    expect(p.bars).toHaveLength(4); // two 5-minute bars a session, no overnight or after-hours
    const first = p.bars[0]!;
    // 09:30-09:34: opens at the first minute's open, highs/lows over five minutes, last close, volume summed
    expect(first).toEqual({ t: ms("2026-11-12", 9, 30) / 1000, o: 100, h: 105, l: 99, c: 104.5, v: 50 });
    expect(p.fetchedAt).not.toBeNull();
  });

  it("is unavailable for a symbol the store holds nothing for", () => {
    seed([{ symbol: "SPX", day: "2026-11-13", hh: 9, mm: 30, px: 7000 }]);
    const p = readProfileBars(config, "NDX", 10);
    expect(p.available).toBe(false);
    expect(p.reason).toMatch(/NDX/);
  });

  it("folds minutes on 5-minute boundaries whatever order they arrive in", () => {
    const t0 = ms("2026-11-13", 9, 30);
    const bars = foldBars([
      { ts_ms: t0 + 5 * 60_000, o: 3, h: 3, l: 3, c: 3, v: 1 },
      { ts_ms: t0 + 60_000, o: 2, h: 9, l: 2, c: 2, v: 1 },
      { ts_ms: t0, o: 1, h: 1, l: 0.5, c: 1, v: 1 },
    ]);
    expect(bars).toEqual([
      { t: t0 / 1000, o: 1, h: 9, l: 0.5, c: 2, v: 2 },
      { t: t0 / 1000 + 300, o: 3, h: 3, l: 3, c: 3, v: 1 },
    ]);
  });

  it("accepts only the recorded symbols and a bounded session count", () => {
    expect(isProfileSymbol("/NQ")).toBe(true);
    expect(isProfileSymbol("../x")).toBe(false);
    expect(sessionCount("5")).toBe(5);
    expect(sessionCount("999")).toBe(40);
    expect(sessionCount("junk")).toBe(10);
    expect(sessionCount("0")).toBe(10);
  });
});
