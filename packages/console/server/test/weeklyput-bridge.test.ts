import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it } from "vitest";
import {
  normaliseCycles,
  normaliseStatus,
  normaliseSummary,
  readWeeklyputCycles,
  readWeeklyputStatus,
  readWeeklyputSummary,
  setWeeklyputCaller,
} from "../src/services/weeklyputBridge.js";

/**
 * The bridge is the one place the module's snake_case becomes camelCase. The shapes below are what
 * `cherrypick.weeklyput.cli status|cycles|summary` print (analytics.py, paper_loop.run_status); a
 * rename on either side fails here. The last block runs the real CLI over a ledger the module's own
 * `db.connect` built, and skips, visibly, where the package is not importable.
 */

afterEach(() => setWeeklyputCaller());

const CYCLES = {
  ok: true,
  cycles: [
    {
      arm: "unhedged",
      expiration: "2026-11-06",
      entry_session: "2026-10-30",
      names: {
        SPX: { status: "closed", strike: 6350, entry_delta: 0.1, entry_value: 184, exit_value: 0, gross_pnl: 184, fees: 0.65, settlement: 0, slippage: 5, net_pnl: 178.35, net_bp: 2.7, exit_reason: "expired", assignment_exposed: false },
        SPY: { status: "closed", strike: 640, entry_delta: 0.1, entry_value: 90, exit_value: -240, gross_pnl: -150, fees: 0.65, settlement: 2, slippage: 3, net_pnl: -155.65, net_bp: -23.9, exit_reason: "assigned", assignment_exposed: true },
      },
      unentered: { IWM: { outcome: "missed", refusal: "delta_not_bracketed" } },
      finished: true,
      cycle_net_bp: -10.6,
      net_pnl: 22.7,
    },
  ],
};

describe("weeklyputBridge normalisation", () => {
  it("maps the calendar", () => {
    const s = normaliseStatus({
      ok: true,
      date: "2026-10-30",
      decision_window: "15:45-15:55 ET",
      entry_today: "2026-11-06",
      next_cycle: "2026-11-06",
      next_entry_session: "2026-10-30",
      arms: ["unhedged"],
      names: ["SPX", "SPY", "QQQ", "IWM"],
      awaiting_close: ["unhedged:SPY:2026-10-30"],
    });
    expect(s).toMatchObject({ decisionWindow: "15:45-15:55 ET", entryToday: "2026-11-06", nextEntrySession: "2026-10-30" });
    expect(s.names).toEqual(["SPX", "SPY", "QQQ", "IWM"]);
    expect(s.awaitingClose).toHaveLength(1);
    expect(normaliseStatus({ ok: true, entry_today: null }).entryToday).toBeNull();
  });

  it("maps a cycle with the module's own mean, and the names it left out", () => {
    const [c] = normaliseCycles(CYCLES);
    expect(c!.cycleNetBp).toBe(-10.6);
    expect(c!.names["SPY"]).toMatchObject({ netBp: -23.9, settlement: 2, assignmentExposed: true, exitReason: "assigned" });
    expect(c!.unentered["IWM"]).toEqual({ outcome: "missed", refusal: "delta_not_bracketed" });
    expect(c!.finished).toBe(true);
  });

  it("keeps an unfinished cycle's mean null, never zero", () => {
    const [c] = normaliseCycles({ cycles: [{ arm: "a", expiration: "2026-11-20", entry_session: "2026-11-13", names: {}, unentered: {}, finished: false, cycle_net_bp: null, net_pnl: null }] });
    expect(c!.cycleNetBp).toBeNull();
    expect(c!.netPnl).toBeNull();
  });

  it("maps the forward read", () => {
    const out = normaliseSummary({
      ok: true,
      arms: {
        unhedged: {
          cycles: 2, mean_bp: 3.1, stdev_bp: 1.2, positive_share: 0.5, worst: { expiration: "2026-11-06", cycle_net_bp: -10.6 },
          by_name: { SPX: { trades: 2, mean_bp: 2.7 } },
          money: { entry_value: 274, exit_value: -240, fees: 1.3, settlement: 2, slippage: 8, gross_pnl: 34, net_pnl: 22.7, fee_drag: 0.012 },
          missed: 1,
        },
      },
    });
    const a = out.arms["unhedged"]!;
    expect(a.meanBp).toBe(3.1);
    expect(a.worst).toEqual({ expiration: "2026-11-06", cycleNetBp: -10.6 });
    expect(a.byName["SPX"]).toEqual({ trades: 2, meanBp: 2.7 });
    expect(a.money.netPnl).toBe(22.7);
    expect(a.missed).toBe(1);
  });

  it("answers ok false, with the reason, when the module cannot be read", async () => {
    setWeeklyputCaller(async () => ({ ok: false, json: null, error: "weeklyput read unavailable — boom" }));
    const out = await readWeeklyputCycles("x.db");
    expect(out).toEqual({ ok: false, error: "weeklyput read unavailable — boom", cycles: [] });
    expect((await readWeeklyputSummary("x.db")).arms).toEqual({});
    expect((await readWeeklyputStatus("x.db")).names).toEqual([]);
  });

  it("passes the verb and the ledger through", async () => {
    const seen: string[] = [];
    setWeeklyputCaller(async (db, verb) => {
      seen.push(`${db}:${verb}`);
      return { ok: true, json: CYCLES, error: null };
    });
    expect((await readWeeklyputCycles("a.db")).cycles).toHaveLength(1);
    expect(seen).toEqual(["a.db:cycles"]);
  });
});

// ---------------------------------------------------------------------------------------------
const PACKAGES = path.resolve(__dirname, "..", "..", "..");
const env = {
  ...process.env,
  PYTHONPATH: [path.join(PACKAGES, "weeklyput", "src"), path.join(PACKAGES, "core", "src"), process.env["PYTHONPATH"] ?? ""]
    .filter(Boolean)
    .join(path.delimiter),
};
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "weeklyput-bridge-test-"));
const ledger = path.join(dir, "paper_trades.db");
const built =
  spawnSync("python", ["-c", `from cherrypick.weeklyput import db; db.connect(r"${ledger}")`], { encoding: "utf-8", timeout: 60_000, env }).status === 0;

describe.skipIf(!built)("the bridge over the module's own CLI", () => {
  it("reads the module's cycle mean for a ledger its own schema built", async () => {
    const db = new Database(ledger);
    const cols = `position_id, arm, symbol, root, settlement_style, expiration, entry_session, strike, entry_forward, entry_value,
      notional, status, exit_value, gross_pnl, fees, settlement, slippage, net_pnl, net_bp`;
    const ins = db.prepare(`INSERT INTO weeklyput_positions (${cols}, streamer_symbol) VALUES (${cols.split(",").map(() => "?").join(",")}, 'x')`);
    ins.run("unhedged:SPX:2026-11-06", "unhedged", "SPX", "SPXW", "cash", "2026-11-06", "2026-10-30", 6350, 6501, 184, 650100, "closed", 0, 184, 0.65, 0, 5, 178.35, 2.7);
    ins.run("unhedged:SPY:2026-11-06", "unhedged", "SPY", "SPY", "physical", "2026-11-06", "2026-10-30", 640, 650, 90, 65000, "closed", -240, -150, 0.65, 2, 3, -155.65, -23.9);
    db.close();
    setWeeklyputCaller();
    process.env["PYTHONPATH"] = env.PYTHONPATH;
    const cycles = await readWeeklyputCycles(ledger);
    expect(cycles.ok).toBe(true);
    expect(cycles.cycles[0]?.cycleNetBp).toBeCloseTo((2.7 + -23.9) / 2, 3);
    const summary = await readWeeklyputSummary(ledger);
    expect(summary.ok).toBe(true);
    expect(summary.arms["unhedged"]?.cycles).toBe(1);
    expect(summary.arms["unhedged"]?.money.netPnl).toBeCloseTo(22.7, 2);
  }, 60_000);
});
