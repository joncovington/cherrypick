import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeic, NO_TRADE_QUERY, meicExitKind } from "../src/readers/meic.js";

/**
 * The trade table standard (root CLAUDE.md) on MEIC's positions and history.
 *
 * MEIC's `pnl` is GROSS and `fees` the total, with `settlement_fees` a component of it (2026-09-25).
 * A row must add up left to right -- entry + exit = gross, gross − fees − settlement = net -- which
 * needs the settlement part taken out of the fee total exactly once. Slippage is inside MEIC's
 * modelled fill prices, so it is shown and never subtracted. The schema is meic's own fixture.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");
const DAY = "2026-09-24";
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-standard-"));
  fs.mkdirSync(path.join(tmp, "meic"));
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);
  const now = `${DAY} 10:00:00-04:00`;
  const ins = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, arm, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, settlement_fees, slippage_dollars, exit_reason,
       put_settle_value, call_settle_value, entry_time, created_at, updated_at)
     VALUES (?, 'SPX', ?, ?, ?, 'advisor', 5950, 6050, 10, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // Settled with the call side in the money: 0.60 cr x2 = +120 entry, gross -380, so exit -500;
  // fees 26.89 of which 10 settlement: trading 16.89, net -406.89.
  ins.run(DAY, "ic-itm", "expired", "a", 0.6, 2, -380, 26.89, 10, 4, "expired_settlement", 0, 5, now, now, now);
  // Expired worthless: keeps the whole credit.
  ins.run(DAY, "ic-otm", "expired", "a", 0.5, 1, 50, 6.89, 0, 2, "expired_settlement", 0, 0, now, now, now);
  // Stopped: a close, no settlement -- but the split was never recorded on this one.
  ins.run(DAY, "ic-stop", "stopped", "b", 0.55, 1, -45, 9.5, null, 3, "stop_triggered", null, null, now, now, now);
  // Still open, and a cancelled entry that never was a position.
  ins.run(DAY, "ic-open", "open", "b", 0.7, 1, null, 3.44, null, 0, null, null, null, now, now, now);
  ins.run(DAY, "ic-void", "cancelled", "b", 0.7, 1, null, null, null, 0, "entry_rejected", null, null, now, now, now);
  db.close();
  config = { paths: { meicDir: path.join(tmp, "meic") } } as unknown as ConsoleConfig;
});

const read = (view: "positions" | "history") => readMeic(config, "paper", { ...NO_TRADE_QUERY, era: "ALL", view });
const row = (view: "positions" | "history", credit: number) => read(view).trades.rows.find((r) => r.netCredit === credit);

describe("MEIC's history rows", () => {
  it("are the closed trades only", () => {
    expect(read("history").trades.total).toBe(3);
  });

  it("sign the entry and exit as whole-position cash flows", () => {
    const r = row("history", 0.6);
    expect(r?.entryCash).toBe(120);
    expect(r?.exitCash).toBe(-500);
  });

  it("add up: entry + exit = gross, gross - fees - settlement = net", () => {
    for (const r of read("history").trades.rows) {
      expect((r.entryCash ?? 0) + (r.exitCash ?? 0)).toBeCloseTo(r.gross ?? NaN, 2);
      expect((r.gross ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0)).toBeCloseTo(r.net ?? NaN, 2);
    }
  });

  it("take settlement out of the fee total exactly once", () => {
    expect(row("history", 0.6)?.fees).toBe(16.89);
    expect(row("history", 0.6)?.settlementFees).toBe(10);
    expect(row("history", 0.6)?.net).toBe(-406.89);
  });

  it("keep the fee total where the split was never recorded", () => {
    expect(row("history", 0.55)?.settlementFees).toBeNull();
    expect(row("history", 0.55)?.fees).toBe(9.5);
  });

  it("name how each trade ended", () => {
    expect(row("history", 0.6)?.exitKind).toBe("settled");
    expect(row("history", 0.5)?.exitKind).toBe("expired");
    expect(row("history", 0.55)?.exitKind).toBe("closed");
  });

  it("total the same way the rows add up, and by arm", () => {
    const t = read("history").totals;
    expect(t.trades).toBe(3);
    expect(t.gross).toBe(-375);
    expect(t.settlementFees).toBe(10);
    expect(t.fees).toBe(33.28);
    expect(t.net).toBe(-418.28);
    expect(t.slippage).toBe(9);
    expect(t.byArm.map((a) => a.arm).sort()).toEqual(["a", "b"]);
    expect(t.byArm.reduce((n, a) => n + a.net, 0)).toBeCloseTo(t.net, 2);
  });
});

describe("MEIC's positions", () => {
  it("are every trade the session held, open or closed, and never a cancelled entry", () => {
    const p = read("positions");
    expect(p.trades.total).toBe(4);
    expect(p.trades.rows.some((r) => r.status === "cancelled")).toBe(false);
  });

  it("carry an open trade's entry, with nothing on the exit side", () => {
    const r = row("positions", 0.7);
    expect(r?.entryCash).toBe(70);
    expect(r?.exitCash).toBeNull();
    expect(r?.net).toBeNull();
  });
});

describe("meicExitKind", () => {
  it("reads a stop on one side and an expiry on the other as settled only when something was ITM", () => {
    expect(meicExitKind(10, "stopped+expired_settlement", 5, null, null)).toBe("settled");
    expect(meicExitKind(10, "stopped+expired_settlement", 0, 0, 0)).toBe("closed");
    expect(meicExitKind(10, "force_close_fomc", null, null, null)).toBe("closed");
    expect(meicExitKind(null, null, null, null, null)).toBeNull();
  });
});
