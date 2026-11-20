import { afterEach, describe, expect, it } from "vitest";
import { normaliseStatus, normaliseSummary, readLadderStatus, readLadderSummary, setLadderCaller } from "../src/services/ladderBridge.js";

/**
 * The bridge is the one place the module's snake_case becomes camelCase. The shapes below are what
 * `cherrypick.ladder.cli status|summary` print (paper_loop.run_status, analytics.summary); a rename on
 * either side fails here.
 */

afterEach(() => setLadderCaller());

const STATUS = {
  ok: true,
  date: "2026-11-20",
  hours: "08:15-20:00 ET",
  in_hours: true,
  arms: { chain: { entry: "chain", target: "fixed" }, decay: { entry: "once", target: "decay" } },
  funds: {
    TQQQ: { bid: 47.41, ask: 47.42, ref_close: 49.01, next_buy: { chain: 45.34, decay: 45.57 } },
    SOXL: { bid: null, ask: null, ref_close: null, next_buy: { chain: null, decay: null } },
  },
  open_lots: [],
};

const SUMMARY = {
  ok: true,
  arms: {
    chain: {
      lots: 8, closed: 6, open: 2, net_pnl: 262.99, gross_pnl: 263.38, fees: 0.39, open_cost: 1937.21, open_marked: 1875.16,
      oldest_open_days: 0, sold_within_18d: 6, in_hospital: 0, symbols: ["SOXL", "TQQQ"], median_hold_days: 1,
      longest_closed_hold_days: 3, unrealised_at_bid: -62.05,
    },
  },
};

describe("ladderBridge normalisation", () => {
  it("maps the funds, each arm's next level, and the hours", () => {
    const s = normaliseStatus(STATUS);
    expect(s).toMatchObject({ date: "2026-11-20", hours: "08:15-20:00 ET", inHours: true });
    expect(s.arms["decay"]).toEqual({ entry: "once", target: "decay" });
    expect(s.funds["TQQQ"]).toEqual({ bid: 47.41, ask: 47.42, refClose: 49.01, nextBuy: { chain: 45.34, decay: 45.57 } });
    expect(s.funds["SOXL"]!.nextBuy["chain"]).toBeNull();
  });

  it("maps the per-arm summary in the group's terms", () => {
    const a = normaliseSummary(SUMMARY).arms["chain"]!;
    expect(a).toMatchObject({ lots: 8, closed: 6, open: 2, netPnl: 262.99, unrealisedAtBid: -62.05, soldWithin18d: 6, inHospital: 0 });
    expect(a.symbols).toEqual(["SOXL", "TQQQ"]);
    expect(a.medianHoldDays).toBe(1);
  });
});

describe("ladderBridge reads", () => {
  it("folds a failed subprocess into ok:false with the reason, never an empty page", async () => {
    setLadderCaller(async () => ({ ok: false, json: null, error: "ladder read unavailable" }));
    const st = await readLadderStatus("x.db");
    expect(st).toMatchObject({ ok: false, error: "ladder read unavailable", funds: {} });
    expect((await readLadderSummary("x.db")).ok).toBe(false);
  });

  it("asks the module for the verb it needs, over the ledger it was given", async () => {
    const seen: string[] = [];
    setLadderCaller(async (db, verb) => {
      seen.push(`${db}:${verb}`);
      return { ok: true, json: verb === "status" ? STATUS : SUMMARY, error: null };
    });
    expect((await readLadderStatus("a.db")).ok).toBe(true);
    expect((await readLadderSummary("a.db")).arms["chain"]!.lots).toBe(8);
    expect(seen).toEqual(["a.db:status", "a.db:summary"]);
  });
});
