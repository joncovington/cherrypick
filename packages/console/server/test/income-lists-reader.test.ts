import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readIncomeList } from "../src/readers/incomeLists.js";

/**
 * Our own income lists: the reader renames `technicals income-lists`' file and decides nothing.
 */

let tmp: string;
let config: ConsoleConfig;

function write(name: string, doc: unknown): void {
  const dir = path.join(tmp, "technicals", "income-lists");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), JSON.stringify(doc));
}

const ROW = {
  symbol: "AAA",
  type: "Stock",
  spot: 100,
  expiry: "2026-12-18",
  dte: 59,
  strike: 88,
  delta: -0.158,
  iv: 0.31,
  bid: 1.2,
  ask: 1.3,
  mid: 1.25,
  working: 1.225,
  premium: 122.5,
  return: 0.01392,
  annual: 0.0861,
  distance: -0.12,
  open_interest: 900,
  volume: 40,
  passes: true,
  quiet: false,
  earnings: "in-trade",
  earnings_date: "2026-11-05",
  earnings_estimated: false,
  ex_dividend_in_trade: null,
};

function doc(session: string, rows: unknown[]) {
  return {
    version: 1,
    session,
    built_at: "2026-10-20T19:10:00Z",
    chain_taken_at: "2026-10-20T18:45:00Z",
    chain_complete: true,
    chain_problem: null,
    rule: { short_put_delta: 0.16, covered_call_delta: 0.16 },
    names: 3,
    lists: { "short-puts": rows, "covered-calls": [{ ...ROW, strike: 110, delta: 0.161, window: "30-45" }] },
    dropped: { "short-puts": { "no chain": 1 }, "covered-calls": {} },
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-income-lists-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
});

describe("readIncomeList", () => {
  it("looks each row's fit up in manage's evaluation of the session, and none without one", () => {
    write("2026-10-20.json", doc("2026-10-20", [ROW, { ...ROW, symbol: "BIG", strike: 900 }, { ...ROW, symbol: "NEW" }]));
    const manage = path.join(tmp, "manage");
    config = { paths: { technicalsDir: path.join(tmp, "technicals"), manageDir: manage } } as unknown as ConsoleConfig;
    const before = readIncomeList(config, "short-puts", null);
    expect(before.fitBasis).toBeNull();
    expect(before.rows.map((r) => r.fit)).toEqual([null, null, null]);
    fs.mkdirSync(manage, { recursive: true });
    const screen_fit = {
      AAA: { short_put: { "88": { bp: 8678, fits: false } }, covered_call: 2 },
      BIG: { short_put: { "900": { bp: 89000, fits: false } }, covered_call: 0 },
    };
    const evalDoc = { accounts: [{ portfolio: { candidates: { account_basis: "cash", available_bp: 8051.34, screen_fit } } }] };
    fs.writeFileSync(path.join(manage, "eval-2026-10-20.json"), JSON.stringify(evalDoc));
    const puts = readIncomeList(config, "short-puts", null);
    expect(puts.fitBasis).toEqual({ session: "2026-10-20", availableBp: 8051.34, basis: "cash" });
    expect(puts.rows.map((r) => [r.symbol, r.fit])).toEqual([
      ["AAA", false],
      ["BIG", false],
      ["NEW", null], // a name manage did not judge stays unjudged
    ]);
    const calls = readIncomeList(config, "covered-calls", null);
    expect(calls.rows.map((r) => [r.symbol, r.fit])).toEqual([["AAA", true]]);
  });

  it("reads the newest session, never a trial, and renames without deciding", () => {
    write("2026-10-19.json", doc("2026-10-19", []));
    write("2026-10-20.json", doc("2026-10-20", [ROW]));
    write("2026-10-21.trial.json", doc("2026-10-21", [ROW, ROW]));
    const p = readIncomeList(config, "short-puts", null);
    expect(p.day).toBe("2026-10-20");
    expect(p.days).toEqual(["2026-10-20", "2026-10-19"]);
    expect(p.targetDelta).toBe(0.16);
    expect(p.dropped).toEqual({ "no chain": 1 });
    expect(p.rows).toHaveLength(1);
    expect(p.rows[0]).toMatchObject({
      symbol: "AAA",
      strike: 88,
      delta: -0.158,
      working: 1.225,
      premium: 122.5,
      returnFraction: 0.01392,
      annualFraction: 0.0861,
      distanceFraction: -0.12,
      openInterest: 900,
      passes: true,
      earnings: "in-trade",
      earningsDate: "2026-11-05",
    });
    expect(readIncomeList(config, "covered-calls", null).rows[0]).toMatchObject({ window: "30-45", strike: 110 });
  });

  it("passes a fund's leveraged badge through, and a list built before it reads none", () => {
    write("2026-10-20.json", doc("2026-10-20", [{ ...ROW, symbol: "TQQQ", leveraged: "leveraged" }, ROW]));
    const rows = readIncomeList(config, "short-puts", null).rows;
    expect(rows.map((r) => [r.symbol, r.leveraged])).toEqual([
      ["TQQQ", "leveraged"],
      ["AAA", null],
    ]);
  });

  it("an older day is reachable; an unknown day falls back to the newest", () => {
    write("2026-10-19.json", doc("2026-10-19", [{ ...ROW, symbol: "OLD" }]));
    write("2026-10-20.json", doc("2026-10-20", [ROW]));
    expect(readIncomeList(config, "short-puts", "2026-10-19").rows[0].symbol).toBe("OLD");
    expect(readIncomeList(config, "short-puts", "2026-01-01").day).toBe("2026-10-20");
  });

  it("an earnings state it does not know is unknown, never clear", () => {
    write("2026-10-20.json", doc("2026-10-20", [{ ...ROW, earnings: "maybe" }]));
    expect(readIncomeList(config, "short-puts", null).rows[0].earnings).toBe("unknown");
  });

  it("passes a call's ex-dividend state and date through; an older list has neither, and a junk state is none", () => {
    const calls = [
      { ...ROW, symbol: "PAST", ex_dividend_in_trade: false, ex_dividend: "past", ex_dividend_date: "2026-08-17" },
      { ...ROW, symbol: "OLD", ex_dividend_in_trade: false },
      { ...ROW, symbol: "JUNK", ex_dividend_in_trade: false, ex_dividend: "clear" },
    ];
    write("2026-10-20.json", { ...doc("2026-10-20", []), lists: { "short-puts": [], "covered-calls": calls } });
    const rows = readIncomeList(config, "covered-calls", null).rows;
    expect(rows.map((r) => [r.symbol, r.exDividend, r.exDividendDate, r.exDividendInTrade])).toEqual([
      ["PAST", "past", "2026-08-17", false],
      ["OLD", null, null, false],
      ["JUNK", null, null, false],
    ]);
  });

  it("nothing built is an empty list, not an error", () => {
    expect(readIncomeList(config, "short-puts", null)).toMatchObject({ day: null, days: [], rows: [] });
  });

  it("passes the fair-value fields through; a list built before them reads null, and a junk state is null", () => {
    const fair = { fair_value: 120, fair_low: 100, fair_high: 140, fair_gap: -0.1667, fair_state: "valued", fair_flags: ["terminal_heavy"] };
    write(
      "2026-10-27.json",
      doc("2026-10-27", [
        { ...ROW, ...fair },
        { ...ROW, symbol: "FUND", fair_value: null, fair_low: null, fair_high: null, fair_gap: null, fair_state: "not-applicable", fair_flags: [] },
        { ...ROW, symbol: "OLD" },
        { ...ROW, symbol: "JUNK", fair_state: "maybe", fair_flags: "x" },
      ]),
    );
    const rows = readIncomeList(config, "short-puts", null).rows;
    expect(rows.map((r) => [r.symbol, r.fair])).toEqual([
      ["AAA", { fairValue: 120, fairLow: 100, fairHigh: 140, fairGap: -0.1667, fairState: "valued", fairFlags: ["terminal_heavy"] }],
      ["FUND", { fairValue: null, fairLow: null, fairHigh: null, fairGap: null, fairState: "not-applicable", fairFlags: [] }],
      ["OLD", null],
      ["JUNK", { fairValue: null, fairLow: null, fairHigh: null, fairGap: null, fairState: null, fairFlags: [] }],
    ]);
  });
});
