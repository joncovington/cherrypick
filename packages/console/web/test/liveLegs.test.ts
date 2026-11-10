import { describe, expect, it } from "vitest";
import type { ManageLiveAccount, ManageLivePayload, ManageLiveTrade, ManagePmccPosition, ManageTrade, ManageWheelCheck } from "@console/shared";
import { intrinsic, liveAccountOf, livePmcc, liveTrade, liveWheelCheck } from "../src/pages/Live/liveLegs";
import { liveGreekLine } from "../src/pages/Live/PortfolioCards";

/**
 * The Live tabs' prices on the stream (liveLegs.ts). A mid or a spot is swapped for its live quote and
 * what manage derives from them by arithmetic is redone its way; every judgement (tested, verdicts,
 * levels, bands) stays the evaluation's, and with no fresh quote the evaluation's figure stands.
 */

function payload(over: Partial<ManageLivePayload> = {}): ManageLivePayload {
  return { session: "2026-11-09", oldestQuoteAt: 0, maxAgeSeconds: 120, closedMarks: false, trades: [], accounts: [], spots: {}, marks: {}, ...over };
}

function check(over: Partial<ManageWheelCheck> = {}): ManageWheelCheck {
  return {
    leg: "2026-11-20 50P",
    symbol: "XYZ   261120P00050000",
    right: "P",
    strike: 50,
    expiration: "2026-11-20",
    dte: 11,
    quantity: -1,
    spot: 52,
    mid: 1.2,
    openPrice: 1,
    wide: null,
    tested: false,
    intrinsic: 0,
    extrinsic: 1.2,
    extrinsicDollars: 120,
    band: null,
    bandMissing: null,
    beyondBand: null,
    restingBtc: [],
    btcPrice: 0.5,
    markAtOrUnderBtc: false,
    ...over,
  };
}

describe("intrinsic", () => {
  it("is a call's spot over its strike and a put's strike over its spot, never below zero", () => {
    expect(intrinsic(55, 50, "C")).toBe(5);
    expect(intrinsic(45, 50, "C")).toBe(0);
    expect(intrinsic(45, 50, "P")).toBe(5);
    expect(intrinsic(null, 50, "P")).toBeNull();
  });
});

describe("liveTrade", () => {
  const t = { tradeId: "XYZ", value: -100, pnlGross: 50, pnlNet: 48, capture: 0.3, fees: -2 } as unknown as ManageTrade;
  const whole: ManageLiveTrade = { tradeId: "XYZ", value: -60, pnlGross: 90, pnlNet: 88, capture: 0.6, atTakeProfit: false, atStop: false, legsQuoted: 2, legsTotal: 2 };

  it("puts the trade on its live value, P&L and capture, keeping the evaluation's net beside it", () => {
    const out = liveTrade(t, whole);
    expect(out.live).toBe(true);
    expect([out.t.value, out.t.pnlGross, out.t.pnlNet, out.t.capture]).toEqual([-60, 90, 88, 0.6]);
    expect(out.evaluatedNet).toBe(48);
    // Rows add up: the live net is the live gross less the evaluation's fees.
    expect(out.t.pnlNet).toBe((out.t.pnlGross ?? 0) + (t.fees ?? 0));
  });

  it("keeps the evaluation's trade when a leg has no fresh quote, or there is no live payload", () => {
    const part = { ...whole, value: null, pnlGross: null, pnlNet: null, capture: null, legsQuoted: 1 };
    expect(liveTrade(t, part)).toEqual({ t, live: false, evaluatedNet: 48 });
    expect(liveTrade(t, undefined).t).toBe(t);
  });
});

describe("liveWheelCheck", () => {
  it("redoes extrinsic and the buy-to-close test on the live mid and spot, and keeps manage's tested", () => {
    const live = payload({ marks: { "XYZ 261120P00050000": 0.45 }, spots: { XYZ: 49 } });
    const r = liveWheelCheck(check(), "XYZ", live);
    expect(r.mid).toBe(0.45);
    expect(r.spot).toBe(49);
    expect(r.intrinsic).toBe(1);
    expect(r.extrinsic).toBeCloseTo(-0.55);
    expect(r.extrinsicDollars).toBeCloseTo(-55);
    expect(r.markAtOrUnderBtc).toBe(true);
    expect(r.tested).toBe(false);
  });

  it("is the evaluation's row, untouched, with no fresh quote for the leg or its name", () => {
    const r = check();
    expect(liveWheelCheck(r, "XYZ", payload())).toBe(r);
    // A payload from a server older than the page: no marks at all.
    expect(liveWheelCheck(r, "XYZ", payload({ marks: undefined }))).toBe(r);
  });
});

describe("livePmcc", () => {
  const p = {
    underlying: "XYZ",
    spot: 16,
    long: { symbol: "XYZ   270319C00013000", strike: 13, mid: 4, intrinsic: 3, extrinsic: 1, extrinsicShare: 0.25 },
    short: { symbol: "XYZ   261218C00019000", strike: 19, mid: 0.3, intrinsic: 0, extrinsic: 0.3, tested: false },
  } as unknown as ManagePmccPosition;

  it("moves the spot and both legs to live quotes, with intrinsic and extrinsic redone as pmcc.py does", () => {
    const out = livePmcc(p, payload({ spots: { XYZ: 17 }, marks: { "XYZ 270319C00013000": 4.6, "XYZ 261218C00019000": 0.4 } }));
    expect(out.spot).toBe(17);
    expect(out.long.mid).toBe(4.6);
    expect(out.long.intrinsic).toBe(4);
    expect(out.long.extrinsic).toBeCloseTo(0.6);
    expect(out.long.extrinsicShare).toBeCloseTo(0.6 / 4.6);
    expect(out.short.mid).toBe(0.4);
    expect(out.short.extrinsic).toBeCloseTo(0.4);
    expect(out.short.tested).toBe(false);
  });

  it("is the evaluation's position with nothing fresh", () => {
    expect(livePmcc(p, payload())).toBe(p);
  });
});

describe("the portfolio's live greeks", () => {
  const acct: ManageLiveAccount = { account: "****1234", bwd: -12.3, theta: 41.5, legsWithGreeks: 6, legsTotal: 6, legsMarked: 6, dayPl: 10, netLiqEstimate: 1000, legsLeftOut: 0 };

  it("finds the account's figures and says the band is the evaluation's", () => {
    const live = payload({ accounts: [acct] });
    expect(liveAccountOf(live, "****1234")).toBe(acct);
    expect(liveAccountOf(live, "****9999")).toBeNull();
    expect(liveGreekLine("theta", acct, "SPY")).toContain("live: $41.50 theta per day");
    expect(liveGreekLine("bwd", acct, "SPY")).toContain("SPY sh beta-weighted delta");
    expect(liveGreekLine("bwd", acct, "SPY")).toContain("the band above is the evaluation's");
  });

  it("says nothing while a leg has no fresh greeks", () => {
    expect(liveGreekLine("theta", { ...acct, theta: null }, "SPY")).toBeNull();
    expect(liveGreekLine("theta", null, "SPY")).toBeNull();
  });
});
