import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readContango, readContangoHistory } from "../src/readers/contango.js";

/**
 * The trade table standard (root CLAUDE.md) on contango's closed stints, against contango's own
 * schema (`fixtures/contango-ledger-schema.sql`, generated from its `db.connect` and pinned by its
 * test_console_schema_fixture.py).
 *
 * A stint buys a fund and later sells it, so the entry is money paid and signs negative and the
 * sale positive; the fund's distributions are a third cash flow. Fills are at mid and slippage is
 * charged, so it is a cost column subtracted once. A share trade records no settlement: `n/r`.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "contango-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-contango-standard-"));
  const dir = path.join(tmp, "contango");
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO contango_positions (position_id, arm, symbol, role, shares, entry_session, entry_mid, entry_value,
       entry_fees, entry_slippage, status, exit_session, exit_mid, exit_value, exit_fees, exit_slippage, exit_reason,
       distributions, gross_pnl, fees, slippage, net_pnl)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // -9950 + 10149 + 0 = 199 gross; 199 - 0.06 - 4.02 = 194.92 net.
  ins.run("control:SVXY:2026-10-06", "control", "SVXY", "risk", 199, "2026-10-06", 50.0, -9950.0, 0, 1.99, "closed",
    "2026-10-07", 51.0, 10149.0, 0.06, 2.03, "switch_to_cash", 0, 199.0, 0.06, 4.02, 194.92);
  // A cash stint with a distribution: -10046.4 + 10050.0 + 12.5 = 16.1 gross; 16.1 - 0.05 - 4.02 = 12.03.
  ins.run("flipexit:SHV:2026-09-28", "flipexit", "SHV", "cash", 91, "2026-09-28", 110.4, -10046.4, 0, 2.01, "closed",
    "2026-10-02", 110.44, 10050.0, 0.05, 2.01, "switch_to_risk", 12.5, 16.1, 0.05, 4.02, 12.03);
  // Open, and a fill whose fees and slippage were never recorded.
  ins.run("control:SHV:2026-10-07", "control", "SHV", "cash", 91, "2026-10-07", 110.4, -10046.4, null, null, "open",
    null, null, null, null, null, null, 0, null, null, null, null);
  db.close();
  config = { paths: { contangoDir: dir, contangoConfigCandidates: [] } } as unknown as ConsoleConfig;
});

const history = (arm: string | null = null, from: string | null = null) =>
  readContangoHistory(config, { arm, range: { from, to: null } });
const row = (id: string) => history().rows.find((r) => r.positionId === id);

describe("contango's closed stints", () => {
  it("list only closed stints, newest close first", () => {
    expect(history().rows.map((r) => r.positionId)).toEqual(["control:SVXY:2026-10-06", "flipexit:SHV:2026-09-28"]);
  });

  it("sign the purchase negative and the sale positive, and add up", () => {
    const r = row("flipexit:SHV:2026-09-28")!;
    expect(r.price).toBe(-110.4);
    expect(r.entryCash).toBe(-10046.4);
    expect(r.exitCash).toBe(10050);
    expect(r.exitKind).toBe("closed");
    expect(r.entryCash! + r.exitCash! + r.distributions!).toBeCloseTo(r.grossPnl!, 2);
    expect(r.grossPnl! - r.fees! - r.slippage!).toBeCloseTo(r.netPnl!, 2);
  });

  it("never records settlement for a share trade", () => {
    for (const r of history().rows) expect(r.settlementFees).toBeNull();
  });

  it("total every matching stint through the same arithmetic", () => {
    const t = history().totals;
    expect(t.positions).toBe(2);
    expect(t.gross).toBe(215.1);
    expect(t.net).toBe(206.95);
    expect(history("control").totals.net).toBe(194.92);
  });
});

describe("contango's history date range", () => {
  it("bounds the close, not the entry", () => {
    expect(history(null, "2026-10-03").rows.map((r) => r.positionId)).toEqual(["control:SVXY:2026-10-06"]);
  });
});

describe("contango's fills", () => {
  it("are signed cash flow, and an unrecorded cost stays null", () => {
    const fills = readContango(config).fills;
    const buy = fills.find((f) => f.side === "buy" && f.session === "2026-10-06")!;
    const sell = fills.find((f) => f.side === "sell" && f.session === "2026-10-07")!;
    expect(buy.value).toBe(-9950);
    expect(sell.value).toBe(10149);
    const unrecorded = fills.find((f) => f.side === "buy" && f.session === "2026-10-07")!;
    expect(unrecorded.fees).toBeNull();
    expect(unrecorded.slippage).toBeNull();
    expect(unrecorded.slippageBps).toBeNull();
  });
});
