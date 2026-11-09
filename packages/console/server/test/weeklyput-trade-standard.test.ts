import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readWeeklyput, readWeeklyputHistory } from "../src/readers/weeklyput.js";
import { readDecisions } from "../src/readers/decisions.js";

/**
 * The trade table standard (docs/conventions/money-layout.md) on weeklyput's settled puts, against a
 * ledger built from the module's schema (`fixtures/weeklyput-ledger-schema.sql`, generated from its
 * `db.connect`).
 *
 * A put is sold at the mid, so entry is the credit (+). Exit is the intrinsic paid at the posted
 * close (-, or 0 when it expires worthless). Fees, settlement and slippage are three columns in the
 * ledger and each is subtracted once. The module owns every one of these figures; the reader only
 * places them, so a column it dropped or double-subtracted fails here.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "weeklyput-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

const COLS = `position_id, arm, symbol, root, settlement_style, expiration, entry_session, strike, quantity, entry_spot,
  entry_forward, entry_mid, entry_delta, entry_value, entry_fees, entry_slippage, notional, status, settle_session,
  settle_price, exit_value, exit_reason, settlement, gross_pnl, fees, slippage, net_pnl, net_bp, assignment_exposed_at`;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-weeklyput-standard-"));
  const dir = path.join(tmp, "weeklyput");
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(`INSERT INTO weeklyput_positions (${COLS}, streamer_symbol) VALUES (${COLS.split(",").map(() => "?").join(",")}, 'x')`);
  // SPX expired worthless: 184 + 0 = 184 gross; 184 - 0.65 - 0 - 5 = 178.35 net.
  ins.run("unhedged:SPX:2026-11-06", "unhedged", "SPX", "SPXW", "cash", "2026-11-06", "2026-10-30", 6350, 1, 6500, 6501,
    1.84, 0.1, 184, 0.65, 5, 650100, "closed", "2026-11-06", 6512.3, 0, "expired", 0, 184, 0.65, 5, 178.35, 2.7, null);
  // SPY assigned: 90 + (-240) = -150 gross; -150 - 0.65 - 2 - 3 = -155.65 net.
  ins.run("unhedged:SPY:2026-11-06", "unhedged", "SPY", "SPY", "physical", "2026-11-06", "2026-10-30", 640, 1, 650,
    650, 0.9, 0.1, 90, 0.65, 3, 65000, "closed", "2026-11-09", 637.6, -240, "assigned", 2, -150, 0.65, 3, -155.65, -23.9,
    "2026-11-05T15:00:00");
  // Open put, expiry still ahead.
  ins.run("unhedged:QQQ:2026-11-20", "unhedged", "QQQ", "QQQ", "physical", "2026-11-20", "2026-11-13", 500, 1, 520,
    520, 1.1, 0.098, 110, 0.65, 2, 52000, "open", null, null, null, null, null, null, null, null, null, null, null);
  const mark = db.prepare(
    "INSERT INTO weeklyput_marks (position_id, ts, session_date, usable, refusal, bid, ask, mid, spot, mark_value, extrinsic) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
  );
  mark.run("unhedged:QQQ:2026-11-20", "2026-11-13T15:50:00", "2026-11-13", 1, null, 1.0, 1.1, 1.05, 520, -105, 1.05);
  mark.run("unhedged:QQQ:2026-11-20", "2026-11-16T10:00:00", "2026-11-16", 1, null, 0.7, 0.8, 0.75, 512, -75, 0.75);
  // A later refused mark still carries a spot; the usable one keeps the mark.
  mark.run("unhedged:QQQ:2026-11-20", "2026-11-16T10:30:00", "2026-11-16", 0, "no_fresh_quote", null, null, null, 510, null, null);
  const ent = db.prepare(
    "INSERT INTO weeklyput_entries (arm, symbol, expiration, entry_session, outcome, refusal, position_id) VALUES (?,?,?,?,?,?,?)",
  );
  ent.run("unhedged", "SPX", "2026-11-06", "2026-10-30", "entered", null, "unhedged:SPX:2026-11-06");
  ent.run("unhedged", "IWM", "2026-11-06", "2026-10-30", "missed", "delta_not_bracketed", null);
  db.prepare("INSERT INTO weeklyput_decisions (trade_date, arm, symbol, mode, reason, accepted, occurrences) VALUES (?,?,?,?,?,?,?)").run(
    "2026-11-13", "unhedged", "IWM", "paper", "delta_not_bracketed", 0, 4,
  );
  db.prepare("INSERT INTO weeklyput_loop_iterations (ran_at, session_date, phase, status) VALUES (?,?,?,?)").run(1_794_000_000, "2026-11-13", "entry", "ok");
  db.close();
  config = { paths: { weeklyputDir: dir } } as unknown as ConsoleConfig;
});

afterAll(() => closePooledDbs());

const history = (from: string | null = null, to: string | null = null) =>
  readWeeklyputHistory(config, { arm: null, range: { from, to } });
const row = (id: string) => history().rows.find((r) => r.positionId === id)!;

describe("weeklyput's settled puts", () => {
  it("list only settled puts, newest expiry first", () => {
    expect(history().rows.map((r) => r.positionId)).toEqual(["unhedged:SPX:2026-11-06", "unhedged:SPY:2026-11-06"]);
  });

  it("sign the credit positive and the intrinsic paid negative, and add up", () => {
    const spy = row("unhedged:SPY:2026-11-06");
    expect(spy.entryCash).toBe(90);
    expect(spy.exitCash).toBe(-240);
    expect(spy.exitKind).toBe("assigned");
    expect(spy.entryCash! + spy.exitCash!).toBeCloseTo(spy.grossPnl!, 2);
    expect(spy.grossPnl! - spy.fees! - spy.settlementFees! - spy.slippage!).toBeCloseTo(spy.netPnl!, 2);
  });

  it("keeps fees, settlement and slippage in their own columns, each charged once", () => {
    const spx = row("unhedged:SPX:2026-11-06");
    expect([spx.fees, spx.settlementFees, spx.slippage]).toEqual([0.65, 0, 5]);
    expect(spx.exitKind).toBe("expired");
    expect(spx.grossPnl! - spx.fees! - spx.settlementFees! - spx.slippage!).toBeCloseTo(spx.netPnl!, 2);
  });

  it("carries the judged measure, net in bp, straight from the ledger", () => {
    expect(row("unhedged:SPX:2026-11-06").netBp).toBe(2.7);
  });

  it("total every matching put through the same arithmetic", () => {
    const t = history().totals;
    expect(t).toEqual({ positions: 2, gross: 34, fees: 1.3, settlementFees: 2, slippage: 8, net: 22.7 });
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });

  it("bound the expiry, not the entry", () => {
    expect(history("2026-11-07").rows).toEqual([]);
    expect(history("2026-11-06", "2026-11-06").rows).toHaveLength(2);
  });
});

describe("weeklyput's ledger read", () => {
  it("marks an open put from its latest usable quote and measures spot from the newest print", () => {
    const q = readWeeklyput(config).positions.find((p) => p.positionId === "unhedged:QQQ:2026-11-20")!;
    expect(q.status).toBe("open");
    expect(q.mark?.value).toBe(-75);
    expect(q.mark?.mid).toBe(0.75);
    expect(q.mark?.spot).toBe(512);
    expect(q.entryValue).toBe(110);
  });

  it("keeps the cycle register: an entered name and a missed one with its refusal", () => {
    const e = readWeeklyput(config).entries;
    expect(e.find((x) => x.symbol === "IWM")).toMatchObject({ outcome: "missed", refusal: "delta_not_bracketed", positionId: null });
    expect(e.find((x) => x.symbol === "SPX")?.positionId).toBe("unhedged:SPX:2026-11-06");
  });

  it("names the loop's last session and iteration", () => {
    const d = readWeeklyput(config);
    expect(d.session).toBe("2026-11-13");
    expect(d.lastIteration).toMatchObject({ phase: "entry", status: "ok" });
  });

  it("reads the decision journal for the loop's last session", () => {
    const out = readDecisions(config, "weeklyput", null);
    expect(out.tradeDate).toBe("2026-11-13");
    expect(out.rows[0]).toMatchObject({ symbol: "IWM", reason: "delta_not_bracketed", occurrences: 4 });
  });

  it("is an empty, absent read where the module never ran", () => {
    const nowhere = { paths: { weeklyputDir: path.join(os.tmpdir(), "weeklyput-nowhere") } } as unknown as ConsoleConfig;
    expect(readWeeklyput(nowhere).dbPresent).toBe(false);
    expect(readWeeklyputHistory(nowhere, { arm: null }).rows).toEqual([]);
  });
});
