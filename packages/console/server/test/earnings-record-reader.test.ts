import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readEarningsRecord } from "../src/readers/earningsRecord.js";

/**
 * The earnings record's reader renames `technicals earnings-record`'s file and decides nothing. What it
 * must get right: the summary carried across, rows newest first, a spread's money and its states
 * kept apart, and no file read as no record rather than an error.
 */

let tmp: string;
let config: ConsoleConfig;

const TRADE = { entry: 200, exit: -60, gross: 140, fees: 4.5, slippage: 5.2, net: 130.3, risk: 302.25 };

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-record-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
  const p = path.join(tmp, "technicals", "earnings-calendar", "record.json");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(
    p,
    JSON.stringify({
      as_of: "2026-10-23",
      summary: {
        announcements: 2,
        measured: 2,
        inside_sd: 0.5,
        median_move_over_sd: 0.875,
        strong_reads: 2,
        direction_right: 0.5,
        spreads_react: { n: 1, net: 130.3, won: 1.0, return_on_risk: 0.4311 },
        spreads_day2: { n: 0, net: 0, won: null, return_on_risk: null },
      },
      rows: [
        {
          symbol: "NFLX",
          date: "2026-10-20",
          calendar_day: "2026-10-19",
          timing: "amc",
          reaction_day: "2026-10-21",
          edge: "sbe",
          edge_label: "strong bear",
          move_pct: -10.0,
          sd_move_pct: 11.0,
          move_over_sd: 0.875,
          inside_sd: true,
          direction_right: true,
          spread: {
            state: "entered",
            kind: "credit",
            right: "C",
            expiry: "2026-11-20",
            strikes: [75, 70],
            entry_mid: 2.0,
            liquid_at_entry: true,
            react: TRADE,
            day2: null,
          },
        },
        {
          symbol: "META",
          date: "2026-10-21",
          calendar_day: "2026-10-20",
          timing: "amc",
          reaction_day: "2026-10-22",
          edge: "sb",
          move_pct: -8.0,
          move_over_sd: 1.2,
          inside_sd: false,
          direction_right: false,
          spread: { state: "not read on the entry session" },
        },
      ],
    }),
  );
});

describe("readEarningsRecord", () => {
  it("carries the summary and the rows, newest first", () => {
    const p = readEarningsRecord(config);
    expect(p.asOf).toBe("2026-10-23");
    expect(p.summary).toMatchObject({ measured: 2, insideSd: 0.5, directionRight: 0.5, react: { n: 1, net: 130.3, won: 1, returnOnRisk: 0.4311 } });
    expect(p.rows.map((r) => r.symbol)).toEqual(["META", "NFLX"]);
  });

  it("keeps a spread's money and its states apart", () => {
    const rows = Object.fromEntries(readEarningsRecord(config).rows.map((r) => [r.symbol, r]));
    expect(rows["NFLX"]!.spread).toMatchObject({ state: "entered", kind: "credit", strikes: [75, 70], react: TRADE, day2: null });
    expect(rows["META"]!.spread).toMatchObject({ state: "not read on the entry session", kind: null, react: null });
    expect(rows["META"]).toMatchObject({ insideSd: false, directionRight: false });
  });

  it("no file is no record, not an error", () => {
    fs.rmSync(tmp, { recursive: true, force: true });
    expect(readEarningsRecord(config)).toEqual({ asOf: null, summary: null, rows: [] });
  });
});
