import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readSkate, skateDays } from "../src/readers/skate.js";

/**
 * The skate reader renames the run file's fields and decides nothing. What it must get right: the
 * newest scheduled run of the newest session by default, a named session on request, a trial run
 * never read; each band's pick found by the best's own expiry and strike (not a neighbour, not the
 * other band's); a missing run, an unreadable file, an incomplete run and a stated problem each
 * said, never an empty table.
 */

let tmp: string;
let config: ConsoleConfig;

function write(day: string, name: string, doc: unknown): void {
  const p = path.join(tmp, "market-report", "screener-greeks", "skate", day, name);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, typeof doc === "string" ? doc : JSON.stringify(doc));
}

function leg(over: Record<string, unknown>): Record<string, unknown> {
  return {
    expiry: "2026-11-20",
    days: 32,
    role: "pick",
    option: "AAA   261120C00110000",
    same_band: false,
    band: "fixed",
    strike: 110,
    offset: 0.004,
    outside_grid: false,
    quote: { bid: 1.2, ask: 1.3, volume: 40, open_interest: 900, updated_at: "x", fetched_at: "y" },
    mid: 1.25,
    iv: 0.31,
    delta: 0.16,
    implied_skate: 0.86,
    skate: 0.9,
    skate_recent: 0.85,
    gap: 0.04,
    yield_bid: 0.12,
    yield_mid: 0.13,
    per_day_bid: 3.75,
    tested_per_cycle: 0.1,
    tested_per_month: 0.11,
    hard: true,
    soft: { skate: true, skate_recent: true, yield: true, all: true },
    ...over,
  };
}

const RUN = {
  measured_at: "2026-10-19T19:05:01+00:00",
  session: "2026-10-19",
  band_day: "2026-10-16",
  complete: false,
  problem: "chain fetch failed for 3 names",
  config: {
    haircut: 0.75,
    by_expiry: [[14, 0.65]],
    rate: 0.04,
    hard: { min_open_interest: 100, max_spread_pct: 0.1, max_spread_abs: 0.05 },
    soft: { skate: 0.84, skate_recent: 0.8, yield: 0.06 },
    stage_two: 60,
    month_days: 35,
  },
  rows: [
    {
      symbol: "AAA",
      side: "C",
      spot: 100,
      fund: false,
      earnings: "2027-01-28",
      div_yield: 0.01,
      stage: 2,
      monthly: "2026-11-20",
      best: {
        fixed: { expiry: "2026-11-20", strike: 110, per_day_bid: 3.75, skate: 0.9, monthly: "2026-11-20", vs_monthly: 1.0 },
        by_expiry: { expiry: "2026-10-30", strike: 106, per_day_bid: 5.5, skate: 0.86, monthly: "2026-11-20", vs_monthly: 1.47 },
      },
      legs: [
        // A neighbour on the best expiry, listed first: never taken for the pick.
        leg({ role: "closer", strike: 109, gap: 0.09 }),
        leg({}),
        // The other band's pick on the same expiry and strike would be a different reading.
        leg({ band: "by_expiry", role: "further", gap: -0.2 }),
        leg({ band: "by_expiry", expiry: "2026-10-30", days: 11, strike: 106, gap: 0.02, per_day_bid: 5.5, hard: true }),
      ],
    },
    {
      symbol: "BBB",
      side: "P",
      spot: 50,
      fund: true,
      earnings: null,
      div_yield: 0,
      stage: 1,
      monthly: null,
      best: { fixed: null, by_expiry: null },
      legs: [leg({ band: "fixed", hard: false })],
    },
    { side: "C" },
  ],
};

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-skate-"));
  config = {
    paths: { technicalsDir: path.join(tmp, "technicals"), marketReportDir: path.join(tmp, "market-report") },
  } as unknown as ConsoleConfig;
});

describe("readSkate", () => {
  it("states no run when nothing has been written", () => {
    expect(readSkate(config)).toMatchObject({ status: "none", day: null, days: [], run: null, rows: [] });
  });

  it("never reads a trial run, and a trial-only day is not a session", () => {
    write("2026-10-18", "1854.trial.json", RUN);
    write("2026-10-19", "1200.json", { ...RUN, measured_at: "2026-10-19T16:00:00+00:00" });
    write("2026-10-19", "1505.json", RUN);
    write("2026-10-19", "1600.trial.json", { ...RUN, measured_at: "trial" });
    fs.mkdirSync(path.join(tmp, "market-report", "screener-greeks", "skate", "chains"), { recursive: true });
    expect(skateDays(config)).toEqual(["2026-10-19"]);
    const p = readSkate(config);
    expect(p).toMatchObject({ status: "ok", day: "2026-10-19", run: "1505", runs: ["1505", "1200"], measuredAt: "2026-10-19T19:05:01+00:00" });
    expect(readSkate(config, "2026-10-18")).toMatchObject({ day: "2026-10-19" });
  });

  it("carries the run's own state, config and verdicts, renamed", () => {
    write("2026-10-19", "1505.json", RUN);
    const p = readSkate(config);
    expect(p).toMatchObject({ bandDay: "2026-10-16", complete: false, problem: "chain fetch failed for 3 names" });
    expect(p.config).toEqual({
      haircut: 0.75,
      byExpiry: [[14, 0.65]],
      hard: { minOpenInterest: 100, maxSpreadPct: 0.1, maxSpreadAbs: 0.05 },
      soft: { skate: 0.84, skateRecent: 0.8, yield: 0.06 },
      stageTwo: 60,
      monthDays: 35,
    });
    expect(p.rows.map((r) => `${r.symbol}${r.side}`)).toEqual(["AAAC", "BBBP"]);
    const aaa = p.rows[0]!;
    expect(aaa.bands.fixed.best).toEqual({ expiry: "2026-11-20", strike: 110, perDayBid: 3.75, skate: 0.9, monthly: "2026-11-20", vsMonthly: 1.0 });
    // The pick, not the closer listed ahead of it, nor the other band's leg on the same strike.
    expect(aaa.bands.fixed.pick).toMatchObject({ role: "pick", strike: 110, gap: 0.04, hard: true, impliedSkate: 0.86, skateRecent: 0.85 });
    expect(aaa.bands.fixed.pick!.quote).toEqual({ bid: 1.2, ask: 1.3, volume: 40, openInterest: 900 });
    expect(aaa.bands.fixed.pick!.soft).toEqual({ skate: true, skateRecent: true, yield: true, all: true });
    expect(aaa.bands.byExpiry.best).toMatchObject({ expiry: "2026-10-30", vsMonthly: 1.47 });
    expect(aaa.bands.byExpiry.pick).toMatchObject({ expiry: "2026-10-30", strike: 106, gap: 0.02 });
    expect(p.rows[1]!.bands).toEqual({ fixed: { best: null, pick: null }, byExpiry: { best: null, pick: null } });
    expect(p.rows[1]).toMatchObject({ fund: true, earnings: null, stage: 1, monthly: null });
  });

  it("says an unreadable run is unreadable, never an empty screen", () => {
    write("2026-10-19", "1505.json", "{ not json");
    expect(readSkate(config)).toMatchObject({ status: "unreadable", day: "2026-10-19", run: "1505", rows: [] });
  });

  it("reads a named session", () => {
    write("2026-10-16", "1505.json", { ...RUN, rows: [] });
    write("2026-10-19", "1200.json", RUN);
    expect(readSkate(config, "2026-10-16")).toMatchObject({ status: "ok", day: "2026-10-16", rows: [] });
    expect(readSkate(config).days).toEqual(["2026-10-19", "2026-10-16"]);
  });
});
