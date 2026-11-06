import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { manageTimeSpreads, readTimeSpreads } from "../src/readers/timeSpreads.js";

/**
 * The time-spread scanner's file and the account's rows: the reader renames the producers' fields and
 * decides nothing (made-up names and figures).
 */

let tmp: string;
let config: ConsoleConfig;

const STRUCTURE = {
  underlying: "XYZ",
  kind: "put calendar",
  direction: "neutral",
  label: "put calendar 2026-11-13/2026-12-18 100/100P",
  front: "2026-11-13",
  back: "2026-12-18",
  spot: 100.5,
  ratio: 1.3,
  iv_rank: 0.5,
  sector: "Tech",
  debit_mid: 2.56,
  debit_natural: 2.7,
  gap: null,
  debit_share_of_gap: null,
  front_iv: 0.6,
  back_iv: 0.45,
  net_delta: 1.2,
  theta_day: 9.5,
  pnl_flat: 120.0,
  pnl_up_5: -40.0,
  pnl_down_10: -150.0,
  loses_unchanged: false,
  breakeven: [94.1, 106.2],
  price_stop: [91.0, null],
  take_profit: 3.2,
  value_stop: 1.28,
  roll_by: "2026-11-10",
  max_loss: 256.0,
  modelled: true,
};

function write(day: string, doc: unknown): void {
  const dir = path.join(tmp, "time-spreads");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${day}.json`), JSON.stringify(doc));
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-time-spreads-"));
  config = { paths: { manageDir: path.join(tmp, "manage") } } as unknown as ConsoleConfig;
});

describe("readTimeSpreads", () => {
  it("says there is no scan yet", () => {
    const got = readTimeSpreads(config, null);
    expect(got.error).toMatch(/no time-spread scan yet/);
    expect(got.structures).toEqual([]);
  });

  it("reads the newest day, passing names first by ratio, then the rest by name", () => {
    write("2026-11-05", { rows: [], structures: [] });
    write("2026-11-06", {
      taken_at: "2026-11-06T15:30:00+00:00",
      complete: true,
      order: "ordered by the front/back IV ratio: a hypothesis, untested (item 54)",
      universe: 4,
      read: 4,
      passing: 2,
      shortlist: ["BBB", "AAA"],
      rows: [
        { symbol: "AAA", ratio: 1.2, passes: true, why: [] },
        { symbol: "ZZZ", ratio: 1.0, passes: false, why: ["front/back ratio under 1.15"] },
        { symbol: "BBB", ratio: 1.5, passes: true, why: [] },
        { symbol: "CCC", ratio: null, passes: false, why: ["no front or back expiry with an IV"] },
      ],
      structures: [STRUCTURE],
      structures_dropped: { "fails the leg bars as one order": 3 },
    });
    const got = readTimeSpreads(config, null);
    expect(got.day).toBe("2026-11-06");
    expect(got.days).toEqual(["2026-11-06", "2026-11-05"]);
    expect(got.terms.map((t) => t.symbol)).toEqual(["BBB", "AAA", "CCC", "ZZZ"]);
    expect(got.structures[0]).toMatchObject({ underlying: "XYZ", debitMid: 2.56, priceStop: [91.0, null], rollBy: "2026-11-10" });
    expect(got.dropped).toEqual({ "fails the leg bars as one order": 3 });
    expect(got.order).toMatch(/untested/);
  });
});

describe("manageTimeSpreads", () => {
  it("is null on an evaluation from before the field", () => {
    expect(manageTimeSpreads(undefined)).toBeNull();
  });

  it("renames the sized rows and the delta filter", () => {
    const got = manageTimeSpreads({
      source_session: "2026-11-06",
      edge: "no tested edge",
      delta_filter: { book_bwd: 150, limit: 100, bullish_held_back: true },
      bp_cap_pct: 0.6,
      fits: [{ ...STRUCTURE, units: 2, binding: null, held_name: false, held_sector: true, bp_after_pct: 0.35 }],
      fits_total: 1,
      too_big: [],
      too_big_total: 0,
      dropped: { "bullish while the book is over its delta limit": 2 },
      error: null,
    })!;
    expect(got.deltaFilter).toEqual({ bookBwd: 150, limit: 100, bullishHeldBack: true });
    expect(got.fits[0]).toMatchObject({ units: 2, heldSector: true, bpAfterPct: 0.35 });
    expect(got.edge).toBe("no tested edge");
  });
});
