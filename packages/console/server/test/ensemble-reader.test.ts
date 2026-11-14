import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { drawdown, readEnsemble, readEnsembleHistory, readEnsemblePerformance } from "../src/readers/ensemble.js";
import { readDecisions } from "../src/readers/decisions.js";
import { readEnsembleSummary } from "../src/readers/summary/ensemble.js";
import { SUMMARY_READERS } from "../src/readers/summary/index.js";

/**
 * ensemble's read layer against a ledger built from the module's own schema
 * (`fixtures/ensemble-ledger-schema.sql`, generated from `db.py`). Invented numbers throughout.
 *
 * The arms are not a fixed list: `alpha` and `zz_custom_arm` trade, and `beta` appears only in the
 * session register and the journal. None is one of the shipped arms, and every list of arms the
 * reader returns must come from these rows.
 *
 * Closed trades, with the running net in exit order (exit session, entry session, arm):
 *   alpha:11-02  +102.40  exit 11-03
 *   alpha:11-03  -127.60  exit 11-04
 *   zz:11-03     -131.90  exit 11-04
 *   alpha:11-04  +101.20  exit 11-05
 *   zz:11-04     +106.60  exit 11-05
 * alpha alone falls 127.60 from its peak; zz alone falls 131.90; stacked, the two losses land on the
 * same day and the fall is 259.50.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "ensemble-ledger-schema.sql"), "utf-8");
const TODAY = "2026-11-13";
const ZZ = "zz_custom_arm";
let tmp: string;
let config: ConsoleConfig;

interface Pos {
  id: string;
  arm: string;
  symbol: string;
  entry: string;
  exit?: string;
  reason?: string;
  entryValue: number;
  exitValue?: number;
  gross?: number;
  fees?: number;
  settlement?: number;
  slippage?: number;
  net?: number;
  stress?: number;
  mid?: number;
  legs: Array<{ name: string; right: string; side: number; strike: number }>;
}

const IC = [
  { name: "short_put", right: "P", side: -1, strike: 5900 },
  { name: "long_put", right: "P", side: 1, strike: 5890 },
  { name: "short_call", right: "C", side: -1, strike: 6100 },
  { name: "long_call", right: "C", side: 1, strike: 6110 },
];
const SPREAD = [
  { name: "long_put", right: "P", side: 1, strike: 215 },
  { name: "short_put", right: "P", side: -1, strike: 212 },
];

const CLOSED: Pos[] = [
  { id: "alpha:2026-11-02", arm: "alpha", symbol: "SPX", entry: "2026-11-02", exit: "2026-11-03", reason: "profit_target", entryValue: 150, exitValue: -40, gross: 110, fees: 2.6, settlement: 0, slippage: 5, net: 102.4, stress: 105.9, mid: 112.6, legs: IC },
  { id: "alpha:2026-11-03", arm: "alpha", symbol: "SPX", entry: "2026-11-03", exit: "2026-11-04", reason: "touch", entryValue: 140, exitValue: -260, gross: -120, fees: 2.6, settlement: 0, slippage: 5, net: -127.6, stress: -124.1, mid: -117.4, legs: IC },
  { id: "alpha:2026-11-04", arm: "alpha", symbol: "SPX", entry: "2026-11-04", exit: "2026-11-05", reason: "settlement", entryValue: 120, exitValue: -10, gross: 110, fees: 2.6, settlement: 1.2, slippage: 5, net: 101.2, stress: 104.7, mid: 111.4, legs: IC },
  { id: `${ZZ}:2026-11-03`, arm: ZZ, symbol: "GLD", entry: "2026-11-03", exit: "2026-11-04", reason: "time", entryValue: -200, exitValue: 80, gross: -120, fees: 3.9, settlement: 0, slippage: 8, net: -131.9, stress: -128, mid: -124, legs: SPREAD },
  { id: `${ZZ}:2026-11-04`, arm: ZZ, symbol: "GLD", entry: "2026-11-04", exit: "2026-11-05", reason: "settlement", entryValue: -210, exitValue: 330, gross: 120, fees: 3.9, settlement: 1.5, slippage: 8, net: 106.6, stress: 110.5, mid: 118.5, legs: SPREAD },
];

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-ensemble-"));
  const dir = path.join(tmp, "ensemble");
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);

  const pos = db.prepare(
    `INSERT INTO ensemble_positions (position_id, arm, symbol, entry_session, expiration, quantity, entry_spot, entry_mid, capital,
       entry_value, entry_fees, entry_slippage, entry_json, status, exit_reason, exit_session, exit_value, gross_pnl, fees, settlement,
       slippage, net_pnl, net_stress, net_mid)
     VALUES (@id, @arm, @symbol, @entry, @exp, 1, 6000, 1.5, 850, @entryValue, 1.3, 2.5, @entryJson, @status, @reason, @exit,
       @exitValue, @gross, @fees, @settlement, @slippage, @net, @stress, @mid)`,
  );
  const leg = db.prepare("INSERT INTO ensemble_legs (position_id, leg_role, right, side, strike, status) VALUES (?,?,?,?,?,?)");
  for (const p of CLOSED) {
    pos.run({
      id: p.id, arm: p.arm, symbol: p.symbol, entry: p.entry, exp: p.exit, entryValue: p.entryValue, entryJson: JSON.stringify({ legs: p.legs }),
      status: "closed", reason: p.reason, exit: p.exit, exitValue: p.exitValue, gross: p.gross, fees: p.fees, settlement: p.settlement,
      slippage: p.slippage, net: p.net, stress: p.stress, mid: p.mid,
    });
    for (const l of p.legs) leg.run(p.id, l.name, l.right, l.side, l.strike, "closed");
  }
  // Open today, with leg rows; and a second open position with none (its legs come from the entry).
  const open = (id: string, arm: string, symbol: string, entryValue: number, legs: typeof IC) =>
    pos.run({
      id, arm, symbol, entry: TODAY, exp: TODAY, entryValue, entryJson: JSON.stringify({ legs }), status: "open", reason: null, exit: null,
      exitValue: null, gross: null, fees: null, settlement: null, slippage: null, net: null, stress: null, mid: null,
    });
  open(`alpha:${TODAY}`, "alpha", "SPX", 130, IC);
  for (const l of IC) leg.run(`alpha:${TODAY}`, l.name, l.right, l.side, l.strike, "open");
  open(`${ZZ}:${TODAY}`, ZZ, "GLD", -190, SPREAD);

  const mark = db.prepare("INSERT INTO ensemble_marks (position_id, ts, session_date, usable, refusal, spot, mark_value) VALUES (?,?,?,?,?,?,?)");
  mark.run(`alpha:${TODAY}`, "2026-11-13T10:00:00", TODAY, 1, null, 6010, -60);
  mark.run(`alpha:${TODAY}`, "2026-11-13T10:30:00", TODAY, 1, null, 6020, -85);
  // A later refused mark carries a spot too, but the usable mark's own spot goes with its value.
  mark.run(`alpha:${TODAY}`, "2026-11-13T11:00:00", TODAY, 0, "no_fresh_quote", 6030, null);

  const ent = db.prepare("INSERT INTO ensemble_entries (arm, symbol, entry_session, outcome, refusal, detail, position_id) VALUES (?,?,?,?,?,?,?)");
  ent.run("alpha", "SPX", TODAY, "entered", null, null, `alpha:${TODAY}`);
  ent.run(ZZ, "GLD", TODAY, "missed", "gate:sd_change", JSON.stringify({ check: "sd_change", value: 0.31 }), null);
  // The register kept the reason but not its detail: the journal's row for that reason supplies it.
  ent.run("beta", "SPX", TODAY, "pending", "gate:gex_ratio", null, null);
  ent.run("alpha", "SPX", "2026-11-12", "missed", "no_tick_in_window", null, null);

  const dec = db.prepare(
    "INSERT INTO ensemble_decisions (trade_date, arm, symbol, mode, reason, accepted, occurrences, first_ts, last_ts, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
  );
  dec.run(TODAY, "alpha", "SPX", "entry", "enter", 1, 1, "2026-11-13T09:45:00", "2026-11-13T09:45:00", JSON.stringify({ legs: "-5900P", quantity: 1 }));
  dec.run(TODAY, ZZ, "GLD", "entry", "gate:sd_change", 0, 4, "2026-11-13T09:40:00", "2026-11-13T09:44:00", JSON.stringify({ check: "sd_change", value: 0.31 }));
  dec.run(TODAY, "beta", "SPX", "entry", "gate:gex_ratio", 0, 7, "2026-11-13T09:40:00", "2026-11-13T09:46:00", JSON.stringify({ check: "gex_ratio", value: 1.8 }));
  db.prepare("INSERT INTO ensemble_loop_iterations (ran_at, session_date, phase, status) VALUES (?,?,?,?)").run(1_794_000_000, TODAY, "entry", "ok");
  db.close();
  config = { paths: { ensembleDir: dir } } as unknown as ConsoleConfig;
});

afterAll(() => closePooledDbs());

const history = (arm: string | null = null, from: string | null = null, to: string | null = null) =>
  readEnsembleHistory(config, { arm, range: { from, to } });

describe("ensemble's closed structures", () => {
  it("list only closed ones, newest exit first", () => {
    expect(history().rows.map((r) => r.positionId)).toEqual([
      "alpha:2026-11-04",
      `${ZZ}:2026-11-04`,
      "alpha:2026-11-03",
      `${ZZ}:2026-11-03`,
      "alpha:2026-11-02",
    ]);
  });

  it("add up on every row: entry + exit = gross, and gross - fees - settlement - slippage = net", () => {
    for (const r of history().rows) {
      expect(r.entryCash! + r.exitCash!).toBeCloseTo(r.grossPnl!, 2);
      expect(r.grossPnl! - r.fees! - r.settlementFees! - r.slippage!).toBeCloseTo(r.netPnl!, 2);
    }
  });

  it("sign a credit positive and a debit negative, and keep the three costs in their own columns", () => {
    const iron = history().rows.find((r) => r.positionId === "alpha:2026-11-04")!;
    expect([iron.entryCash, iron.exitCash]).toEqual([120, -10]);
    expect([iron.fees, iron.settlementFees, iron.slippage]).toEqual([2.6, 1.2, 5]);
    expect(iron.exitKind).toBe("settled");
    const debit = history().rows.find((r) => r.positionId === `${ZZ}:2026-11-03`)!;
    expect([debit.entryCash, debit.exitCash]).toEqual([-200, 80]);
    expect(debit.exitKind).toBe("closed");
    expect(debit.exitReason).toBe("time");
  });

  it("carry the stress and mid nets as context beside the net", () => {
    const r = history().rows.find((x) => x.positionId === "alpha:2026-11-03")!;
    expect([r.netPnl, r.netStress, r.netMid]).toEqual([-127.6, -124.1, -117.4]);
  });

  it("carry every leg, in the order the ledger holds them", () => {
    const r = history().rows.find((x) => x.positionId === "alpha:2026-11-02")!;
    expect(r.legs.map((l) => `${l.side > 0 ? "+" : "-"}${l.strike}${l.right}`)).toEqual(["-5900P", "+5890P", "-6100C", "+6110C"]);
  });

  it("total every matching structure through the same arithmetic", () => {
    const t = history().totals;
    expect(t).toEqual({ positions: 5, gross: 100, fees: 15.6, settlementFees: 2.7, slippage: 31, net: 50.7 });
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });

  it("filter by arm and bound the exit session", () => {
    expect(history(ZZ).rows).toHaveLength(2);
    expect(history(null, "2026-11-05").rows.map((r) => r.exitSession)).toEqual(["2026-11-05", "2026-11-05"]);
    expect(history(null, "2026-11-06").rows).toEqual([]);
  });
});

describe("ensemble's net and drawdown", () => {
  it("measures drawdown as the largest peak-to-trough fall of the running sum", () => {
    expect(drawdown([])).toBe(0);
    expect(drawdown([5, 10])).toBe(0);
    expect(drawdown([10, -4, -3, 2])).toBe(7);
    expect(drawdown([-5, 3, -1])).toBe(5);
  });

  it("gives each arm its own net and drawdown", () => {
    const p = readEnsemblePerformance(config);
    expect(p.arms["alpha"]).toMatchObject({ trades: 3, net: 76, wins: 2, drawdown: 127.6, worst: -127.6 });
    expect(p.arms[ZZ]).toMatchObject({ trades: 2, net: -25.3, wins: 1, drawdown: 131.9 });
  });

  it("stacks the arms for the combined line, in exit order", () => {
    const c = readEnsemblePerformance(config).combined;
    expect(c).toMatchObject({ trades: 5, net: 50.7, gross: 100, fees: 15.6, settlement: 2.7, slippage: 31, drawdown: 259.5 });
    expect(c.curve.map((p) => p.y)).toEqual([102.4, -25.2, -157.1, -55.9, 50.7]);
    // Neither arm's own fall is the combined one: the two losses land on the same day.
    expect(c.drawdown).toBeGreaterThan(Math.max(readEnsemblePerformance(config).arms["alpha"]!.drawdown, readEnsemblePerformance(config).arms[ZZ]!.drawdown));
  });

  it("shows the stress and mid nets beside the verdict net", () => {
    const a = readEnsemblePerformance(config).arms["alpha"]!;
    expect([a.net, a.netStress, a.netMid]).toEqual([76, 86.5, 106.6]);
    expect(a.gross - a.fees - a.settlement - a.slippage).toBeCloseTo(a.net, 2);
  });

  it("derives its arms from the ledger, including one the module does not ship, and an arm with no trade yet", () => {
    const p = readEnsemblePerformance(config);
    expect(Object.keys(p.arms)).toEqual(["alpha", "beta", ZZ]);
    expect(p.arms["beta"]).toMatchObject({ trades: 0, net: 0, drawdown: 0, mean: null, worst: null });
    expect(readEnsemble(config).arms).toEqual(["alpha", "beta", ZZ]);
  });
});

describe("ensemble's open structures", () => {
  const open = () => readEnsemble(config).positions.filter((p) => p.status === "open");

  it("mark from the latest usable quote, spot and value from the same tick", () => {
    const a = open().find((p) => p.arm === "alpha")!;
    expect(a.mark).toMatchObject({ value: -85, spot: 6020 });
    // Unrealised is the credit taken in plus what closing would bring: 130 + (-85).
    expect(a.unrealised).toBe(45);
    expect(a.entryValue).toBe(130);
    expect(a.legs).toHaveLength(4);
  });

  it("read the legs from the entry when the ledger holds no leg rows, and leave an unmarked one unrealised-less", () => {
    const z = open().find((p) => p.arm === ZZ)!;
    expect(z.legs.map((l) => `${l.side > 0 ? "+" : "-"}${l.strike}${l.right}`)).toEqual(["+215P", "-212P"]);
    expect(z.mark).toBeNull();
    expect(z.unrealised).toBeNull();
  });
});

describe("ensemble's day", () => {
  it("names the loop's last session and iteration", () => {
    const d = readEnsemble(config);
    expect(d.session).toBe(TODAY);
    expect(d.lastIteration).toMatchObject({ phase: "entry", status: "ok" });
  });

  it("lists each arm that was due today, and no earlier session's row", () => {
    const t = readEnsemble(config).today;
    expect(t.map((r) => [r.arm, r.outcome])).toEqual([
      ["alpha", "entered"],
      ["beta", "pending"],
      [ZZ, "missed"],
    ]);
  });

  it("names the gate that blocked a refused arm, with the value it read", () => {
    const t = readEnsemble(config).today;
    expect(t.find((r) => r.arm === ZZ)!.gate).toEqual({ reason: "gate:sd_change", check: "sd_change", value: "0.31", occurrences: 4 });
    // The register kept no detail for beta; the journal's row for the same reason does.
    expect(t.find((r) => r.arm === "beta")!.gate).toEqual({ reason: "gate:gex_ratio", check: "gex_ratio", value: "1.8", occurrences: 7 });
  });

  it("shows no gate on an arm that entered", () => {
    expect(readEnsemble(config).today.find((r) => r.arm === "alpha")!.gate).toBeNull();
  });

  it("reads the decision journal for the loop's last session", () => {
    const out = readDecisions(config, "ensemble", null);
    expect(out.tradeDate).toBe(TODAY);
    expect(out.rows[0]).toMatchObject({ arm: "beta", reason: "gate:gex_ratio", occurrences: 7 });
    expect(out.rows.at(-1)).toMatchObject({ arm: "alpha", reason: "enter", accepted: true });
  });

  it("is an empty, absent read where the module never ran", () => {
    const nowhere = { paths: { ensembleDir: path.join(os.tmpdir(), "ensemble-nowhere") } } as unknown as ConsoleConfig;
    expect(readEnsemble(nowhere).dbPresent).toBe(false);
    expect(readEnsemble(nowhere).arms).toEqual([]);
    expect(readEnsembleHistory(nowhere, { arm: null }).rows).toEqual([]);
    expect(readEnsemblePerformance(nowhere).combined.trades).toBe(0);
  });
});

describe("ensemble's simplified summary", () => {
  const q = { mode: "paper" as const, range: "all", day: null };

  it("is registered, and its hero equals the history totals", () => {
    expect(SUMMARY_READERS["ensemble"]).toBe(readEnsembleSummary);
    const s = readEnsembleSummary(config, q);
    const t = history().totals;
    expect(s.hero.trades).toBe(5);
    expect(s.hero.net).toBeCloseTo(t.net, 2);
    expect(s.hero.gross).toBeCloseTo(t.gross, 2);
    expect(s.hero.open).toBe(2);
  });

  it("lists the arms the ledger names", () => {
    expect(readEnsembleSummary(config, q).arms.map((a) => a.arm).sort()).toEqual(["alpha", ZZ]);
  });

  it("says there is nothing yet where there is no ledger", () => {
    const nowhere = { paths: { ensembleDir: path.join(os.tmpdir(), "ensemble-nowhere") } } as unknown as ConsoleConfig;
    expect(readEnsembleSummary(nowhere, q).notes[0]).toContain("No ensemble ledger");
  });
});
