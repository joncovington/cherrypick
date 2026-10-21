import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { NO_TRADE_QUERY, meicEntryCash, meicTradeCash, readMeic } from "../src/readers/meic.js";
import { totalsOf } from "../src/readers/meicLive.js";
import { meicLoopState } from "../src/readers/liveToday.js";

/**
 * Two live MEIC rows the 2026-10-21 accuracy review caught, as they stand in the live ledger:
 *
 * - row 6 (2026-06-29, stopped): gross −$2.00, `fees` NULL. Its net is unknown, so it reads n/r --
 *   never gross less a $0 fee -- and every total over it says n/r, with the recorded share beside it.
 * - row 7 (2026-06-30, stopped): `net_credit` 0.43 (the confirmed fill, set by the 2026-10-14 ledger
 *   correction), side credits 0.14 + 0.215 left from quote time, exit 0.39, gross +$4.00. The entry is
 *   the net credit the gross was booked against, so entry + exit = gross.
 *
 * Plus the today tab's MEIC loop cell, read off the supervisor's `meic-live` job.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");

const ROW6 = { net_credit: 0.38, put_credit: null, call_credit: null, quantity: 1, dollar_multiplier: 100, pnl: -2, fees: null, settlement_fees: 0, exit_reason: "stop_triggered" };
const ROW7 = { net_credit: 0.43, put_credit: 0.14, call_credit: 0.215, quantity: 1, dollar_multiplier: 100, pnl: 4, fees: 4.96, settlement_fees: 0, exit_reason: "software_stop_triggered" };

describe("meicTradeCash on a row that never recorded its fees", () => {
  it("leaves fees and net n/r rather than charging $0", () => {
    const c = meicTradeCash(ROW6);
    expect(c.gross).toBe(-2);
    expect(c.fees).toBeNull();
    expect(c.net).toBeNull();
    expect(c.entryCash).toBe(38);
    expect(c.exitCash).toBe(-40);
  });

  it("still nets a row that did record them", () => {
    expect(meicTradeCash(ROW7).net).toBe(-0.96);
  });
});

describe("the entry when the side credits disagree with the fill", () => {
  it("takes the net credit the gross was booked against, so the row adds up", () => {
    const c = meicTradeCash(ROW7);
    expect(c.entryCash).toBe(43);
    expect(c.exitCash).toBe(-39);
    expect((c.entryCash ?? 0) + (c.exitCash ?? 0)).toBeCloseTo(c.gross ?? NaN, 2);
  });

  it("keeps the per-side rounding when the sides do sum to the net (within half a cent)", () => {
    // A worthless expiry: sides 0.125 + 0.13 against a cent-rounded net of 0.26.
    expect(meicEntryCash(0.26, 0.125, 0.13, 100, 1)).toBe(25.5);
    expect(meicEntryCash(0.22, 0.14, 0.08, 100, 1)).toBe(22);
    expect(meicEntryCash(0.43, 0.14, 0.215, 100, 1)).toBe(43);
    expect(meicEntryCash(null, 0.14, 0.215, 100, 1)).toBe(35.5);
  });
});

describe("totals over a row that never recorded its fees", () => {
  const rows = [ROW6, ROW7].map((r, i) => ({
    mode: "live" as const,
    id: i + 6,
    tradeDate: i === 0 ? "2026-06-29" : "2026-06-30",
    entryTime: null,
    symbol: "XSP",
    putStrike: null,
    callStrike: null,
    wingWidth: null,
    netCredit: r.net_credit,
    quantity: 1,
    status: "stopped",
    exitReason: r.exit_reason,
    ivRankAtEntry: null,
    ...meicTradeCash(r),
  }));

  it("the live tab's totals read fees and net n/r, with the recorded share beside them", () => {
    const t = totalsOf(rows);
    expect(t.trades).toBe(2);
    expect(t.gross).toBe(2);
    expect(t.fees).toBeNull();
    expect(t.net).toBeNull();
    expect(t.feesTrades).toBe(1);
    expect(t.feesRecorded).toBe(4.96);
    expect(t.netRecorded).toBe(-0.96);
  });

  it("are whole again when every row recorded them", () => {
    const t = totalsOf(rows.slice(1));
    expect(t.fees).toBe(4.96);
    expect(t.net).toBe(-0.96);
  });

  let config: ConsoleConfig;
  beforeAll(() => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-fees-"));
    fs.mkdirSync(path.join(tmp, "meic"));
    const db = new Database(path.join(tmp, "meic", "meic_trades.db"));
    db.exec(SCHEMA);
    const ins = db.prepare(
      `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike, wing_width,
         net_credit, put_credit, call_credit, quantity, pnl, fees, settlement_fees, exit_reason, entry_time, created_at, updated_at)
       VALUES (?, 'XSP', ?, 'stopped', 'live', 'book', 735, 744, 3, ?, ?, ?, 1, ?, ?, 0, 'stop_triggered', ?, ?, ?)`,
    );
    const at = "2026-06-29 10:54:00-04:00";
    ins.run("2026-06-29", "ic-6", 0.38, null, null, -2, null, at, at, at);
    ins.run("2026-06-30", "ic-7", 0.43, 0.14, 0.215, 4, 4.96, at, at, at);
    db.close();
    config = { paths: { meicDir: path.join(tmp, "meic") } } as unknown as ConsoleConfig;
  });

  it("the MEIC page's totals do the same, by arm too", () => {
    const p = readMeic(config, "live", { ...NO_TRADE_QUERY, era: "ALL", view: "history", from: "2026-06-01", to: "2026-07-01" });
    const t = p.totals;
    expect(t.trades).toBe(2);
    expect(t.gross).toBe(2);
    expect(t.fees).toBeNull();
    expect(t.net).toBeNull();
    expect(t.feesTrades).toBe(1);
    expect(t.feesRecorded).toBe(4.96);
    expect(t.netRecorded).toBe(-0.96);
    expect(t.byArm[0]?.net).toBeNull();
    expect(t.byArm[0]?.netRecorded).toBe(-0.96);
    const r6 = p.trades.rows.find((r) => r.netCredit === 0.38);
    expect(r6?.net).toBeNull();
    const r7 = p.trades.rows.find((r) => r.netCredit === 0.43);
    expect(r7?.entryCash).toBe(43);
    expect(r7?.exitCash).toBe(-39);
  });
});

describe("the today tab's MEIC loop state", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-loop-"));
  fs.mkdirSync(path.join(home, "state"));
  const cfg = { paths: { cherrypick: home } } as unknown as ConsoleConfig;
  const write = (jobs: Record<string, unknown>) =>
    fs.writeFileSync(path.join(home, "state", "supervisor-jobs.json"), JSON.stringify({ schema: 1, jobs }));
  const now = new Date("2026-10-21T15:00:00Z");

  it("is null when the supervisor names no meic-live job", () => {
    write({ watchdog: {} });
    expect(meicLoopState(cfg, now)).toBeNull();
  });

  it("is live while a tick ran in the last two minutes", () => {
    write({ "meic-live": { running_pid: null, last_start: "2026-10-21T14:59:00+00:00", last_exit_at: "2026-10-21T14:59:30+00:00" } });
    expect(meicLoopState(cfg, now)).toEqual({ state: "live", lastAt: "2026-10-21T14:59:30+00:00", ageSeconds: 30 });
  });

  it("is idle when the last tick is old (not armed, or the job stopped)", () => {
    write({ "meic-live": { enabled: false, running_pid: null, last_exit_at: "2026-10-19T06:20:23+00:00" } });
    expect(meicLoopState(cfg, now)?.state).toBe("idle");
  });

  it("says no iterations when the job has never run", () => {
    write({ "meic-live": { running_pid: null, last_start: null, last_exit_at: null } });
    expect(meicLoopState(cfg, now)?.state).toBe("no-data");
  });
});
