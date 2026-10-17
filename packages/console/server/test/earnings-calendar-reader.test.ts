import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readEarningsCalendar } from "../src/readers/earningsCalendar.js";

/**
 * The earnings tab's reader renames the calendar file's fields and decides nothing. What it must get
 * right: the newest day by default and a named one on request; every field carried across, an
 * unknown time of day left null; a suggestion only where the file has a whole one; and an absent
 * folder read as no calendar, not an error.
 */

let tmp: string;
let config: ConsoleConfig;

function write(day: string, doc: unknown): void {
  const p = path.join(tmp, "technicals", "earnings-calendar", `${day}.json`);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(doc));
}

const NFLX = {
  symbol: "NFLX",
  company: "Netflix, Inc.",
  date: "2026-10-20",
  timing: "amc",
  timing_source: "dolt",
  date_source: "broker",
  date_confirmed: true,
  date_gap_days: 0,
  entry_session: "2026-10-20",
  reaction_day: "2026-10-21",
  type: "Stock",
  region: "US",
  edge: "sbe",
  edge_label: "strong bear",
  strength: 2,
  iv_rank: 74,
  liquidity_rank: 1,
  optionable: true,
  sector: "Communication Services",
  price: 70.53,
  event_expiry: "2026-10-23",
  event_iv: 0.85571,
  sd_move: 8.36,
  sd_move_pct: 11.85,
  em_move: 5.67,
  suggestion: { kind: "credit", right: "C", short: 70.53, long: 76.1984, expiry: "2026-11-20" },
};

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-calendar-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
  write("2026-10-16", {
    session: "2026-10-16",
    settings: { iv_rank_cut: 50 },
    inputs: { security_list: "2026-10-16", expiry_iv: "2026-10-16", broker_dates: "2026-10-16", nasdaq_times: "2026-10-16", close_date: "2026-10-16" },
    rows: [
      NFLX,
      {
        ...NFLX,
        symbol: "META",
        timing: null,
        timing_source: null,
        usual_timing: "amc",
        entry_session: null,
        edge: "sb",
        suggestion: { kind: "credit" },
      },
      { symbol: "BROKEN" },
    ],
  });
  write("2026-10-15", { session: "2026-10-15", rows: [{ ...NFLX, symbol: "OLD" }] });
});

describe("readEarningsCalendar", () => {
  it("reads the newest day by default, a named one on request", () => {
    const p = readEarningsCalendar(config);
    expect(p.day).toBe("2026-10-16");
    expect(p.days).toEqual(["2026-10-16", "2026-10-15"]);
    expect(p.ivRankCut).toBe(50);
    expect(p.inputs).toEqual({ securityList: "2026-10-16", expiryIv: "2026-10-16", brokerDates: "2026-10-16", nasdaqTimes: "2026-10-16", closeDate: "2026-10-16" });
    expect(readEarningsCalendar(config, "2026-10-15").rows.map((r) => r.symbol)).toEqual(["OLD"]);
  });

  it("carries every field across and drops a row without its date", () => {
    const rows = Object.fromEntries(readEarningsCalendar(config).rows.map((r) => [r.symbol, r]));
    expect(Object.keys(rows)).toEqual(["NFLX", "META"]);
    expect(rows["NFLX"]).toMatchObject({
      company: "Netflix, Inc.",
      timing: "amc",
      timingSource: "dolt",
      dateSource: "broker",
      dateConfirmed: true,
      entrySession: "2026-10-20",
      edge: "sbe",
      edgeLabel: "strong bear",
      strength: 2,
      ivRank: 74,
      liquidityRank: 1,
      region: "US",
      sdMovePct: 11.85,
      eventExpiry: "2026-10-23",
      suggestion: { kind: "credit", right: "C", short: 70.53, long: 76.1984, expiry: "2026-11-20" },
    });
    // No time of day is null, never a guess; a half-written suggestion is none.
    expect(rows["META"]).toMatchObject({ timing: null, usualTiming: "amc", entrySession: null, suggestion: null });
  });

  it("an absent folder is no calendar, not an error", () => {
    fs.rmSync(tmp, { recursive: true, force: true });
    expect(readEarningsCalendar(config)).toMatchObject({ day: null, days: [], rows: [] });
  });
});
