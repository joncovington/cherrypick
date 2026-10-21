import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageAccount, ManageTrade, ManageWheel, ManageWheelCheck, ManageWheelCycle } from "@console/shared";
import { NO_WHEEL, WheelCards } from "../src/pages/Live/WheelCards";

/**
 * Live → wheel draws packages/manage's wheel view and decides nothing. The guards: an account with no
 * cycle says so plainly (and why, when the view has an error); an unreconciled cycle lists its
 * problems and shows no wheel rule; shares that predate the history show no basis, never a number;
 * and "tested" is manage's flag, never re-derived from the price and the strike.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function check(over: Partial<ManageWheelCheck> = {}): ManageWheelCheck {
  return {
    leg: "2026-11-20 50P",
    symbol: "XYZ 261120P00050000",
    right: "P",
    strike: 50,
    expiration: "2026-11-20",
    dte: 38,
    quantity: -1,
    spot: 50,
    mid: 1.2,
    openPrice: 1,
    wide: null,
    tested: true,
    intrinsic: 0,
    extrinsic: 1.2,
    extrinsicDollars: 120,
    band: null,
    bandMissing: "no band for the name in the snapshot",
    beyondBand: null,
    restingBtc: [],
    btcPrice: null,
    markAtOrUnderBtc: null,
    ...over,
  };
}

/** The open put cycle packages/manage's own test (test_wheel_rules) evaluates, as the reader shapes it. */
function cycle(over: Partial<ManageWheelCycle> = {}): ManageWheelCycle {
  return {
    underlying: "XYZ",
    status: "open",
    reconciled: true,
    problems: [],
    flat: false,
    start: "2026-10-01",
    startKnown: true,
    end: null,
    days: 12,
    assignments: 0,
    calledAway: 0,
    shares: 0,
    basisKnown: true,
    lots: [],
    openOptions: [{ symbol: "XYZ 261120P00050000", leg: "2026-11-20 50P", quantity: -1 }],
    credits: 100,
    debits: 0,
    netCash: 100,
    fees: -1.1,
    netPremium: 100,
    dividends: 0,
    taxBasis: null,
    wheelBasis: null,
    breakeven: { price: 49.011, basis: "put strike less net cash" },
    optionPnl: { realised: 0, unrealised: -20 },
    stockPnl: { realised: 0, unrealised: 0 },
    pnlNet: -21.1,
    skateYield: { value: -0.128, capped: false, capital: 5000, basis: "comparison figure: net P&L / time-weighted capital x 365 / days" },
    events: [{ date: "2026-10-01", what: "open", legs: ["STO 1 2026-11-20 50P @ 1.00"], cash: 100, fees: -1.1 }],
    today: [check()],
    tradeIds: ["XYZ"],
    ...over,
  };
}

function trade(over: Partial<ManageTrade> = {}): ManageTrade {
  return {
    tradeId: "XYZ",
    underlying: "XYZ",
    spot: 50,
    kind: "credit",
    shape: "naked put",
    intent: null,
    note: null,
    cost: null,
    value: null,
    pnlGross: -20,
    pnlNet: null,
    fees: null,
    basis: "order_chain",
    chain: null,
    capture: null,
    nearestShortExpiry: "2026-11-20",
    nearestShortDte: 38,
    action: "ROLL_OR_CLOSE",
    confidence: 50,
    rule: "wheel.tested",
    reason: "tested",
    explanation: null,
    agreeing: [],
    conflicts: [],
    replaces: [],
    roll: null,
    fired: [
      { rule: "wheel.tested", action: "ROLL_OR_CLOSE", confidence: 50, reason: "the 2026-11-20 50P is tested (50 at or through the strike)", legs: null },
      { rule: "credit.time_exit", action: "REVIEW", confidence: 75, reason: "not a wheel rule", legs: null },
    ],
    legs: [],
    opening: null,
    order: null,
    takeProfit: null,
    stop: null,
    timeExit: null,
    earnings: null,
    exDividend: null,
    profile: null,
    earningsTrade: null,
    heldForEarnings: [],
    wheel: [
      {
        underlying: "XYZ",
        start: "2026-10-01",
        status: "open",
        problems: null,
        takeProfit: [{ leg: "2026-11-20 50P", mid: 1.2, btcPrice: 0.03, source: "the default", hit: false }],
        rolls: [
          {
            leg: "2026-11-20 50P",
            expiration: "2026-12-18",
            quotedAt: "2026-10-12T18:45:00Z",
            why: null,
            netZero: {
              closePrice: 1.2,
              open: { strike: 47, bid: 1.7, ask: 1.9, mid: 1.8 },
              net: 0.6,
              expiration: "2026-12-18",
              legs: ["BTC 1 2026-11-20 50P @ 1.20", "STO 1 2026-12-18 47P @ 1.80 (quoted 1.70/1.90)"],
              check: { verdict: "ok", credit: 60, maxLossBefore: 4850, maxLossAfter: 4490, shapeBefore: "naked put", shapeAfter: "naked put", problems: [], notes: [], after: [] },
              checkError: null,
            },
            netZeroWhy: null,
            premiumGambit: null,
            premiumGambitWhy: "the price the short was sold at is unknown",
          },
        ],
        nextLeg: null,
      },
    ],
    ...over,
  };
}

function wheel(over: Partial<ManageWheel> = {}): ManageWheel {
  return { cycles: [cycle()], flagged: [], outside: {}, bandsDay: null, bandsMissing: "the snapshot carries no skate bands", error: null, ...over };
}

function account(w: ManageWheel | null, trades: ManageTrade[] = [trade()]): ManageAccount {
  return {
    account: "****1234",
    netLiq: 100000,
    maintenance: null,
    buyingPower: null,
    riskBudget: null,
    pnlGross: null,
    counts: {},
    trades,
    excluded: [],
    portfolio: null,
    wheel: w,
  };
}

function render(a: ManageAccount): string {
  return text(renderToString(<WheelCards a={a} />));
}

describe("the wheel tab", () => {
  it("says plainly when no wheel is running, and why when the view has an error", () => {
    const empty = render(account(wheel({ cycles: [] })));
    expect(empty).toContain(NO_WHEEL);
    expect(empty).not.toContain("no wheel view for this account");
    const failed = render(account(wheel({ cycles: [], error: "the trade history is the designated account's" })));
    expect(failed).toContain(NO_WHEEL);
    expect(failed).toContain("no wheel view for this account: the trade history is the designated account's");
  });

  it("an evaluation from before the wheel view says so, rather than claiming no wheel runs", () => {
    const out = render(account(null));
    expect(out).toContain("written before the wheel view");
    expect(out).not.toContain(NO_WHEEL);
  });

  it("an open cycle shows manage's money in the suite's layout, its check and its wheel rules", () => {
    const out = render(account(wheel()));
    expect(out).toContain("XYZ · wheel from 2026-10-01");
    expect(out).toContain("net cash +$100.00");
    expect(out).toContain("fees paid $1.10");
    expect(out).toContain("breakeven 49.01 a share (put strike less net cash)");
    expect(out).toContain("P&L net -$21.10");
    expect(out).toContain("option, unrealised -$20.00");
    expect(out).toContain("comparison figure");
    expect(out).toContain("-12.8%");
    // Today's check: no band in the snapshot, said so; nothing resting.
    expect(out).toContain("band not in the snapshot (no band for the name in the snapshot)");
    expect(out).toContain("none resting");
    expect(out).toContain("1.20 a share · $120.00");
    // The wheel rule that fired, and only the wheel's: the roll candidates with their verdicts.
    expect(out).toContain("wheel.tested");
    expect(out).not.toContain("credit.time_exit");
    expect(out).toContain("net-zero 0.60 cr a share");
    expect(out).toContain("check-roll ok");
    expect(out).toContain("premium gambit none: the price the short was sold at is unknown");
    expect(out).toContain("buy-to-close 0.03 (the default), marked 1.20: above it");
  });

  it("an unreconciled cycle lists its problems and shows no wheel rule", () => {
    const c = cycle({ status: "unreconciled", reconciled: false, problems: ["a close the history never saw opened", "an unmatched delivery"] });
    const t = trade({
      fired: [{ rule: "wheel_data", action: "REVIEW", confidence: null, reason: "the wheel cycle does not reconcile", legs: null }],
      wheel: [{ underlying: "XYZ", start: "2026-10-01", status: "unreconciled", problems: c.problems, takeProfit: null, rolls: null, nextLeg: null }],
    });
    const out = render(account(wheel({ cycles: [c] }), [t]));
    expect(out).toContain("unreconciled");
    expect(out).toContain("a close the history never saw opened");
    expect(out).toContain("an unmatched delivery");
    expect(out).toContain("no wheel rule judged");
    expect(out).not.toContain("wheel_data");
    expect(out).not.toContain("check-roll");
    expect(out).not.toContain("fired");
  });

  it("shares that predate the history show no basis, never a number", () => {
    const c = cycle({
      shares: 100,
      basisKnown: false,
      startKnown: false,
      // Even were a figure there, the page must not print it as a basis.
      taxBasis: 42.5,
      wheelBasis: 41.25,
      breakeven: null,
      stockPnl: { realised: null, unrealised: null },
      pnlNet: null,
      lots: [{ date: null, shares: 100, price: null, source: "before the history" }],
    });
    const out = render(account(wheel({ cycles: [c] })));
    expect(out).toContain("tax basis unknown, shares predate the history");
    expect(out).toContain("wheel basis unknown, shares predate the history");
    expect(out).not.toContain("42.50");
    expect(out).not.toContain("41.25");
    expect(out).toContain("at least 12 (shares predate the history)");
    expect(out).toContain("P&L net unknown");
  });

  it("tested is manage's flag, never worked out from the price and the strike", () => {
    // A put at 50 with the price at 60 is not tested by arithmetic; manage's flag says it is.
    const flagged = render(account(wheel({ cycles: [cycle({ today: [check({ spot: 60, tested: true })] })] })));
    expect(flagged).toMatch(/60\.00 1\.20 tested /);
    // And the other way: through the strike by arithmetic, not tested by the flag.
    const unflagged = render(account(wheel({ cycles: [cycle({ today: [check({ spot: 45, tested: false })] })] })));
    expect(unflagged).toMatch(/45\.00 1\.20 not tested /);
    // No price: neither tested nor not.
    const unknown = render(account(wheel({ cycles: [cycle({ today: [check({ spot: null, tested: null })] })] })));
    expect(unknown).toMatch(/— 1\.20 no price /);
    expect(unknown).not.toMatch(/1\.20 (not )?tested /);
  });
});
