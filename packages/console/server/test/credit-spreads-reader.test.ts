import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readCreditSpreadList } from "../src/readers/incomeLists.js";

/**
 * Our credit-spread list: the reader renames `technicals income-lists`' credit-spread rows
 * (`creditspreads.build_rows`, file version 2) and decides nothing.
 */

let tmp: string;
let config: ConsoleConfig;

function write(name: string, doc: unknown): void {
  const dir = path.join(tmp, "technicals", "income-lists");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), JSON.stringify(doc));
}

// As the builder writes a spread: per share, a credit +.
const SPREAD = {
  long: 94,
  long_option: "AAA   261120P00094000",
  long_delta: -0.3,
  width: 2,
  credit: 0.74,
  credit_mid: 0.78,
  credit_width: 0.37,
  max_loss: 1.26,
  credit_max_loss: 0.5873,
  passes: true,
};

const ROW = {
  symbol: "AAA",
  type: "Stock",
  spread: "put",
  setups: ["pullback"],
  signals: [{ setup: "pullback", side: "P", date: "2026-10-21", ago: 2 }],
  conflict: false,
  spot: 100,
  expiry: "2026-11-20",
  dte: 31,
  move: 5.13,
  short: { strike: 96, option: "AAA   261120P00096000", delta: -0.398, iv: 0.31, bid: 1.9, ask: 1.98 },
  pick: SPREAD,
  widths: [
    { unit: "strikes", size: 1, ...SPREAD, long: 95, width: 1, credit: 0.45, credit_width: 0.45 },
    { unit: "strikes", size: 5 }, // the chain does not reach it
    { unit: "move", size: 0.5, ...SPREAD },
  ],
  earnings: "clear",
  earnings_date: "2027-01-28",
  earnings_estimated: false,
  ex_dividend: null,
  ex_dividend_date: null,
  iv_index: 0.3,
  leveraged: null,
};

function doc(session: string, spreads: unknown[] | undefined) {
  return {
    version: spreads === undefined ? 1 : 2,
    session,
    built_at: "2026-10-23T19:10:00Z",
    chain_taken_at: "2026-10-23T18:45:00Z",
    chain_complete: true,
    chain_problem: null,
    rule: {
      short_put_delta: 0.16,
      credit_spreads: { short_delta: 0.4, credit_share: [0.35, 0.4], recent_sessions: 5, tested: "not yet: item 17" },
    },
    names: 488,
    lists: { "short-puts": [], "covered-calls": [], ...(spreads === undefined ? {} : { "credit-spreads": spreads }) },
    dropped: { "credit-spreads": { "no fresh setup entry": 370, "no 30-45 day monthly": 3 } },
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-credit-spreads-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
});

describe("readCreditSpreadList", () => {
  it("renames the builder's rows, its pick, its width table and its rule", () => {
    write("2026-10-23.json", doc("2026-10-23", [ROW]));
    const p = readCreditSpreadList(config, null);
    expect(p).toMatchObject({ day: "2026-10-23", built: true, names: 488 });
    expect(p.rule).toEqual({
      shortDelta: 0.4,
      creditShare: [0.35, 0.4],
      recentSessions: 5,
      tested: "not yet: item 17",
      selections: [], // a list built before selections names none
    });
    expect(p.dropped).toEqual({ "no fresh setup entry": 370, "no 30-45 day monthly": 3 });
    const r = p.rows[0]!;
    expect(r).toMatchObject({ symbol: "AAA", spread: "put", setups: ["pullback"], dte: 31, move: 5.13 });
    expect(r.signals).toEqual([{ setup: "pullback", date: "2026-10-21", ago: 2 }]);
    expect(r.short).toEqual({ strike: 96, delta: -0.398, iv: 0.31, bid: 1.9, ask: 1.98 });
    expect(r.pick).toEqual({
      long: 94,
      longDelta: -0.3,
      width: 2,
      credit: 0.74,
      creditMid: 0.78,
      creditWidth: 0.37,
      maxLoss: 1.26,
      creditMaxLoss: 0.5873,
      passes: true,
    });
    expect(r.widths.map((w) => [w.unit, w.size, w.price?.long ?? null])).toEqual([
      ["strikes", 1, 95],
      ["strikes", 5, null], // a width the chain does not reach keeps its row, with no price
      ["move", 0.5, 94],
    ]);
    expect(r.exDividend).toBeNull(); // a put spread carries none
  });

  it("a row with no 35-40% width keeps its row and its table", () => {
    write("2026-10-23.json", doc("2026-10-23", [{ ...ROW, pick: null }]));
    const r = readCreditSpreadList(config, null).rows[0]!;
    expect(r.pick).toBeNull();
    expect(r.widths).toHaveLength(3);
  });

  it("a call spread's ex-dividend state passes through; a junk one is unknown, never clear", () => {
    const call = { ...ROW, spread: "call", ex_dividend: "in-trade", ex_dividend_date: "2026-11-02" };
    write("2026-10-23.json", doc("2026-10-23", [call, { ...call, symbol: "JUNK", ex_dividend: "clear" }]));
    const rows = readCreditSpreadList(config, null).rows;
    expect(rows.map((r) => [r.symbol, r.exDividend, r.exDividendDate])).toEqual([
      ["AAA", "in-trade", "2026-11-02"],
      ["JUNK", "unknown", "2026-11-02"],
    ]);
  });

  it("an unknown earnings state is unknown, and a row missing its short is dropped", () => {
    write("2026-10-23.json", doc("2026-10-23", [{ ...ROW, earnings: "maybe" }, { ...ROW, symbol: "BAD", short: {} }]));
    const rows = readCreditSpreadList(config, null).rows;
    expect(rows.map((r) => [r.symbol, r.earnings])).toEqual([["AAA", "unknown"]]);
  });

  it("a session built before the list existed is not built, never an empty list", () => {
    write("2026-10-22.json", doc("2026-10-22", undefined));
    write("2026-10-23.json", doc("2026-10-23", []));
    expect(readCreditSpreadList(config, "2026-10-22")).toMatchObject({ day: "2026-10-22", built: false, rows: [] });
    expect(readCreditSpreadList(config, null)).toMatchObject({ day: "2026-10-23", built: true, rows: [] });
  });

  it("nothing built is an empty payload, not an error", () => {
    expect(readCreditSpreadList(config, null)).toMatchObject({ day: null, days: [], built: false, rows: [] });
  });
});

describe("the credit-spread selections (2026-10-25)", () => {
  it("names each selection and parses each row's own short and spread, null where it does not apply", () => {
    const pick = { long: 83, long_option: "P83", width: 5, credit: 1.1, credit_mid: 1.15, credit_width: 0.22, max_loss: 3.9, credit_max_loss: 0.28, passes: true };
    const row = {
      ...ROW,
      selections: {
        "credit-share": null,
        "reversion-d20-s5": { short: { strike: 88, delta: -0.2, iv: 0.3, bid: 1.2, ask: 1.3 }, ...pick },
      },
    };
    const d = doc("2026-10-23", [row]);
    d.rule.credit_spreads.selections = { "reversion-d20-s5": { label: "mean-reversion puts, 0.20 delta, 5 strikes", rule: "r" } };
    write("2026-10-23.json", d);
    const p = readCreditSpreadList(config, null);
    expect(p.rule.selections).toEqual([{ id: "reversion-d20-s5", label: "mean-reversion puts, 0.20 delta, 5 strikes", rule: "r" }]);
    const sel = p.rows[0]!.selections!;
    expect(sel["credit-share"]).toBeNull();
    expect(sel["reversion-d20-s5"]).toMatchObject({ short: { strike: 88, delta: -0.2 }, price: { long: 83, width: 5 } });
  });
});

