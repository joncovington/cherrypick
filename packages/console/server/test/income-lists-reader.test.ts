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

  it("nothing built is an empty list, not an error", () => {
    expect(readIncomeList(config, "short-puts", null)).toMatchObject({ day: null, days: [], rows: [] });
  });
});
