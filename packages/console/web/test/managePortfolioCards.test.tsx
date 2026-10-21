import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import type { ManageCandidate, ManageCandidateRoom, ManageCandidates, ManagePortfolio } from "@console/shared";
import { IncomeCard } from "../src/pages/Live/IncomeCard";
import { BandDial, PortfolioCards } from "../src/pages/Live/PortfolioCards";
import { BuyingPowerCard, LadderCard, RiskGraphCard, RiskMatrixCard, ScenarioCards } from "../src/pages/Live/ScenarioCards";

/**
 * The portfolio cards draw packages/manage's reading and decide nothing. The guards: in-band or
 * outside is the reading's flag, never the dial's own arithmetic; long and short delta carry no tone;
 * a hedge appears only when the reading carries one; an evaluation without a reading draws nothing.
 */

/** Rendered HTML without the text separators React writes between adjacent strings. */
function render(el: ReactElement): string {
  return renderToString(el).replace(/<!-- -->/g, "");
}

function reading(over: Partial<ManagePortfolio> = {}): ManagePortfolio {
  return {
    benchmark: "SPY",
    benchmarkSpot: 763.28,
    complete: true,
    unmodelledLegs: [],
    noBeta: [],
    accountKind: { marginOrCash: "Cash", typeName: "Traditional IRA" },
    netLiq: 14273.72,
    bwd: 5.85,
    bwdDollarsPer1pct: 44.63,
    theta: 43.68,
    thetaPctNetLiq: 0.00306,
    thetaExFront: 43.68,
    frontDte: 2,
    vega30: -8.87,
    breakeven: { kind: "short_gamma", points: 15.22, pct: 0.01994 },
    expectedMovePct: 0.01026,
    expectedMove: 7.83,
    realisedMove: 4.17,
    buyingPowerUsed: 7622.5,
    buyingPowerUsedPct: 0.534,
    stress: [
      { shock: -0.1, pnl: -1569.16, pctNetLiq: -0.1099, leftOut: [] },
      { shock: 0.05, pnl: 23.12, pctNetLiq: 0.0016, leftOut: [] },
    ],
    bands: {
      delta: { low: -21.84, high: 32.76, basis: "theta", value: 5.85, inside: true },
      theta: { low: 0.001, high: 0.003, value: 0.00306, inside: false },
      buyingPower: { low: 0, high: 0.3, value: 0.534, inside: false, vix: 19.99 },
    },
    vix: 19.99,
    scenarios: {
      moves: [-0.1, 0, 0.1],
      ivShiftWeighting: "term",
      matrix: [
        { ivShift: -0.05, pnl: [-1759, 34, -74] },
        { ivShift: 0, pnl: [-1570, 0, -106] },
        { ivShift: 0.05, pnl: [-1437, -48, -102] },
      ],
      graph: {
        moves: [-0.15, -0.05, 0, 0.05, 0.15],
        curves: [
          { horizonDays: 0, pnl: [-2580, -537, 0, 23, -227] },
          { horizonDays: 4, pnl: [-2533, -180, 537, 143, -4] },
        ],
      },
      leftOut: [],
    },
    ladder: {
      expiries: [
        { expiration: "2026-10-23", trades: ["RTX", "NFLX", "UAL", "CLF"], downside: -1064, downsideUnknown: 0, theta: 15.16 },
        { expiration: "2026-11-20", trades: ["META", "GOOGL"], downside: -6837, downsideUnknown: 1, theta: 77.27 },
      ],
      earnings: {
        trades: ["RTX", "META"],
        theta: 35,
        thetaShare: 0.724,
        downside: -4292.5,
        byDate: [
          { date: "2026-10-21", trades: ["RTX"], downside: -1292.5 },
          { date: "2026-10-28", trades: ["META"], downside: -3000 },
        ],
      },
    },
    guidance: [
      { rule: "portfolio.theta", confidence: 40, reason: "above the ceiling", drivers: [{ underlying: "GOOGL", figures: [{ kind: "theta", value: 9.06 }] }] },
    ],
    ruleConfidence: { low: 40, high: 40 },
    underlyings: [
      { underlying: "SPY", beta: 1, bwd: -9.13, theta: 5.93, vega30: 9.84, bwdShare: 0.164, thetaShare: 0.128, betaDoubt: null },
      { underlying: "META", beta: 1.6, bwd: 6.22, theta: 6.42, vega30: -10.06, bwdShare: 0.112, thetaShare: 0.138, betaDoubt: null },
      { underlying: "FXI", beta: 0.27, bwd: -0.61, theta: -1.34, vega30: 3.93, bwdShare: 0.011, thetaShare: 0.029, betaDoubt: "opposite signs" },
    ],
    hedges: null,
    candidates: null,
    brokerCheck: null,
    ...over,
  };
}

describe("BandDial", () => {
  it("takes in-band from the reading's flag, never from where the needle sits", () => {
    // 5.85 sits inside -21.84..32.76 numerically; the flag says outside, and the flag wins.
    const html = render(
      <BandDial band={{ low: -21.84, high: 32.76, value: 5.85, inside: false }} unit="SPY sh" label="delta" />,
    );
    expect(html).toContain("pf-needle-out");
    const inside = render(
      <BandDial band={{ low: -21.84, high: 32.76, value: 99, inside: true }} unit="SPY sh" label="delta" />,
    );
    expect(inside).not.toContain("pf-needle-out");
  });

  it("writes the true value even when the needle pins the scale", () => {
    const html = render(<BandDial band={{ low: -5, high: 5, value: 400, inside: false }} unit="SPY sh" label="delta" />);
    expect(html).toContain("+400.0");
  });

  it("says there is nothing to place rather than drawing a needle at zero", () => {
    const html = render(<BandDial band={{ low: -5, high: 5, value: null, inside: null }} unit="SPY sh" label="delta" />);
    expect(html).toContain("no delta to place");
    expect(html).not.toContain("<svg");
  });
});

describe("PortfolioCards", () => {
  it("draws nothing for an evaluation written before the reading existed", () => {
    expect(render(<PortfolioCards p={null} />)).toBe("");
  });

  it("shows the check against the broker's model only when the reading carries one, untoned", () => {
    expect(render(<PortfolioCards p={reading()} />)).not.toContain("against the broker");
    const html = render(
      <PortfolioCards
        p={reading({
          brokerCheck: {
            legsCompared: 30,
            legsLeftOut: ["-1 GOOGL 370C"],
            bwdOurs: 5.85,
            bwdBroker: 7.1,
            bwdGap: -1.21,
            thetaOurs: 43.68,
            thetaBroker: 41.2,
            thetaGap: 2.48,
            underlyings: [{ underlying: "META", legs: 4, bwdGap: -0.8, thetaGap: 0.4 }],
            legsOnBrokerIv: 7,
            expiring: null,
          },
        })}
      />,
    );
    expect(html).toContain("against the broker");
    expect(html).toContain("−1.2 sh"); // the delta gap
    expect(html).toContain("30 legs on both models");
    expect(html).toContain("the broker&#x27;s IV on 7 with a wide quote");
    expect(html).toContain("-1 GOOGL 370C");
    expect(html).not.toContain("expiring today");
  });

  it("shows the legs expiring today as their own line under the check", () => {
    const html = render(
      <PortfolioCards
        p={reading({
          brokerCheck: {
            legsCompared: 38,
            legsLeftOut: [],
            bwdOurs: 19.18,
            bwdBroker: 24.77,
            bwdGap: -5.59,
            thetaOurs: 13.14,
            thetaBroker: 27.62,
            thetaGap: -14.48,
            underlyings: [],
            legsOnBrokerIv: null,
            expiring: {
              legs: ["-2 2026-10-21 160C", "+1 2026-10-21 162.5C"],
              bwdOurs: 0,
              bwdBroker: -30.69,
              bwdGap: 30.69,
              thetaOurs: 180.5,
              thetaBroker: 554.55,
              thetaGap: -374.05,
            },
          },
        })}
      />,
    );
    expect(html).toContain("expiring today, not in the figures above");
    expect(html).toContain("-2 2026-10-21 160C, +1 2026-10-21 162.5C");
    expect(html).toContain("+30.7 sh");
    expect(html).not.toContain("wide quote"); // an older evaluation has no count, so says nothing
  });

  it("shows each band's chip from the reading and the guidance it fired", () => {
    const html = render(<PortfolioCards p={reading()} />);
    expect(html).toContain("in band"); // delta
    expect(html).toContain("outside band"); // theta
    expect(html).toContain("portfolio.theta");
    expect(html).toContain("SPY −10%");
    expect(html).toContain("Traditional IRA");
  });

  it("states the theta the band reads beside the whole book's, and only the whole on an older reading", () => {
    const split = render(<PortfolioCards p={reading({ theta: 213.02, thetaExFront: 1.63, frontDte: 2 })} />);
    expect(split).toContain("$1.63 a day without legs 2 days or less from expiry, $213.02 with them");
    const old = render(<PortfolioCards p={reading({ thetaExFront: null, frontDte: null })} />);
    expect(old).toContain("$43.68 a day");
    expect(old).not.toContain("with them");
  });

  it("states each guidance driver in its own unit, and a concentration driver as both shares", () => {
    const html = render(
      <PortfolioCards
        p={reading({
          guidance: [
            { rule: "portfolio.theta", confidence: 40, reason: "above", drivers: [{ underlying: "GOOGL", figures: [{ kind: "theta", value: 9.06 }] }] },
            { rule: "portfolio.delta", confidence: 40, reason: "outside", drivers: [{ underlying: "META", figures: [{ kind: "bwd", value: 6.22 }] }] },
            {
              rule: "portfolio.concentration",
              confidence: 40,
              reason: "over 25%",
              drivers: [{ underlying: "SPY", figures: [{ kind: "bwd_share", value: 0.31 }, { kind: "theta_share", value: 0.128 }] }],
            },
          ],
        })}
      />,
    );
    expect(html).toContain("+$9.06/day");
    expect(html).toContain("+6.2 SPY sh");
    expect(html).toContain("31% of delta · 13% of theta");
    expect(html).not.toContain("+0.3<"); // never a share printed as a signed number
  });

  it("states the ruleset's own confidence in the caption", () => {
    const at55 = render(<PortfolioCards p={reading({ ruleConfidence: { low: 55, high: 55 } })} />);
    expect(at55).toContain("retail heuristics at confidence 55");
    expect(at55).not.toContain("confidence 40:");
    expect(render(<PortfolioCards p={reading({ ruleConfidence: { low: 35, high: 40 } })} />)).toContain("at confidence 35–40");
    expect(render(<PortfolioCards p={reading({ ruleConfidence: null })} />)).toContain("retail heuristics: no published evidence");
  });

  it("draws long and short delta without a sign tone", () => {
    const html = render(<PortfolioCards p={reading()} />);
    const delta = html.slice(html.indexOf("where the delta comes from"), html.indexOf("where the theta comes from"));
    expect(delta).toContain("diverge-neutral");
    expect(delta).not.toContain("pnl-neg");
    expect(delta).toContain("FXI ⚠");
  });

  it("sums the names past the cut into one row, so the rows add up to the book", () => {
    const base = reading();
    const extra = Array.from({ length: 14 }, (_, i) => ({ ...base.underlyings[0]!, underlying: `N${i}`, bwd: 14 - i, betaDoubt: null }));
    const html = render(<PortfolioCards p={reading({ underlyings: extra })} />);
    const delta = html.slice(html.indexOf("where the delta comes from"), html.indexOf("where the theta comes from"));
    // Twelve names shown (14 down to 3), the last two (2 and 1) as one row of +3.
    expect(delta).toContain("2 others");
    expect(delta).toContain("+3.0 sh");
    expect(delta).not.toContain(">N13<");
  });

  it("shows a hedge only when the reading carries one, with its natural price beside the mid", () => {
    expect(render(<PortfolioCards p={reading()} />)).not.toContain("a hedge, sized");
    const hedged = reading({
      hedges: {
        target: 27.3,
        need: -4.76,
        root: "XSP",
        expiration: "2026-12-18",
        dte: 60,
        error: null,
        suggestions: [
          {
            kind: "credit",
            structure: "call credit spread",
            error: null,
            units: 1,
            legs: [
              { label: "XSP 2026-12-18 785C", quantity: -1, bid: 4.56, ask: 5.86, mid: 5.21, delta: 0.29 },
              { label: "XSP 2026-12-18 790C", quantity: 1, bid: 3.0, ask: 4.27, mid: 3.635, delta: 0.23 },
            ],
            priceLabel: "1.58 cr",
            natural: 0.29,
            cash: 157.5,
            maxLoss: 342.5,
            bwdChange: -6.42,
            thetaChange: 2.76,
            pastCap: false,
            after: { bwd: -0.57, inBand: true, theta: 46.44, thetaPctNetLiq: 0.00325, breakevenPct: 0.0182, buyingPowerUsedPct: 0.558 },
          },
          { kind: "debit", structure: "put debit spread", error: "one spread ties up $158, past the 50% buying-power cap ($0 left)", units: null, legs: [], priceLabel: null, natural: null, cash: null, maxLoss: null, bwdChange: null, thetaChange: null, pastCap: false, after: null },
        ],
      },
    });
    const html = render(<PortfolioCards p={hedged} />);
    expect(html).toContain("a hedge, sized");
    expect(html).toContain("1.58 cr");
    expect(html).toContain("natural 0.29 cr");
    expect(html).toContain("sell 1 × XSP 2026-12-18 785C");
    expect(html).toContain("buying-power cap");
  });

  it("says when a debit hedge is offered past the cap", () => {
    const base = reading();
    const pastCap = reading({
      hedges: {
        target: 1, need: -4.76, root: "XSP", expiration: "2026-11-20", dte: 33, error: null,
        suggestions: [
          { kind: "debit", structure: "put debit spread", error: null, units: 1, legs: [], priceLabel: "1.58 db", natural: -2.66,
            cash: -158, maxLoss: 158, bwdChange: -4.75, thetaChange: 0.12, pastCap: true,
            after: { bwd: 1.1, inBand: true, theta: 43.8, thetaPctNetLiq: 0.003, breakevenPct: 0.022, buyingPowerUsedPct: 0.5451 } },
        ],
      },
    });
    expect(render(<PortfolioCards p={base} />)).not.toContain("past the buying-power cap");
    expect(render(<PortfolioCards p={pastCap} />)).toContain("past the buying-power cap: offered anyway");
  });
});

describe("ScenarioCards", () => {
  it("takes buying power's in/out from the reading's flag and names the VIX behind the cap", () => {
    const html = render(<BuyingPowerCard p={reading()} />);
    expect(html).toContain("outside band");
    expect(html).toContain("VIX 20.0 allows up to 30%");
    // A cap the reading did not set is not invented.
    const blind = reading({ bands: { ...reading().bands!, buyingPower: { low: 0, high: null, value: 0.534, inside: null, vix: null } } });
    expect(render(<BuyingPowerCard p={blind} />)).toContain("no VIX reading");
  });

  it("tints the matrix by sign, outlines its origin and orders IV-up on top", () => {
    const html = render(<RiskMatrixCard p={reading()} />);
    expect(html.indexOf("IV +5 pts")).toBeLessThan(html.indexOf("IV unchanged"));
    expect(html.indexOf("IV unchanged")).toBeLessThan(html.indexOf("IV −5 pts"));
    expect(html).toContain("pf-matrix-origin");
    expect(html).toContain("var(--neg)");
    expect(html).toContain("-$1,437");
    expect(html).toContain("scaled to each leg");
  });

  it("draws one line per horizon with a hover column per move", () => {
    const html = render(<RiskGraphCard p={reading()} />);
    expect(html.match(/<polyline/g)).toHaveLength(2);
    expect(html.match(/<rect /g)).toHaveLength(5);
    expect(html).toContain("at nearest expiry (+4d): +$537");
  });

  it("lists what expires when and calls out the earnings exposure", () => {
    const html = render(<LadderCard ladder={reading().ladder} />);
    expect(html).toContain("2026-11-20 · 2 trades");
    expect(html).toContain("(+1 not modelled)");
    expect(html).toContain("72% of theta");
    expect(html).toContain("by report date:");
    expect(html).toContain("(RTX)");
    expect(html).toContain("(META)");
    expect(render(<LadderCard ladder={null} />)).toBe("");
  });

  it("names a date carrying only long legs of trades filed elsewhere, rather than 0 trades", () => {
    const l = reading().ladder!;
    const lone = { expiration: "2029-01-19", trades: [], downside: 0, downsideUnknown: 0, theta: -0.77 };
    const html = render(<LadderCard ladder={{ ...l, expiries: [...l.expiries, lone] }} />);
    expect(html).toContain("2029-01-19 · long legs only");
    expect(html).not.toContain("0 trades");
  });

  it("draws nothing it has no reading for", () => {
    const html = render(<ScenarioCards p={reading({ scenarios: null, ladder: null })} />);
    expect(html).not.toContain("risk matrix");
    expect(html).not.toContain("risk graph");
    expect(html).toContain("buying power");
  });
});

function candidate(over: Partial<ManageCandidate> = {}): ManageCandidate {
  return {
    underlying: "AAPL",
    kind: "call_spread",
    alternativeTo: null,
    expiry: "2026-11-20",
    dte: 33,
    legs: [
      { strike: 345, right: "C", quantity: -1, bid: 3.4, ask: 3.6, delta: 0.45 },
      { strike: 350, right: "C", quantity: 1, bid: 0.95, ask: 1.1, delta: 0.33 },
    ],
    units: 2,
    binding: "per-trade risk",
    perUnit: { credit: 2.47, natural: 2.3, cash: 247, maxLoss: 253, buyingPower: 253, stressLoss: 253, theta: 0.93, bwd: 1.5 },
    thetaPerBp: 0.0037,
    earningsDate: "2026-12-04",
    earningsUnknown: false,
    alternative: null,
    ifRoom: null,
    leveraged: null,
    heldSameExpiry: false,
    odds: null,
    ...over,
  };
}

function room(over: Partial<ManageCandidateRoom> = {}): ManageCandidateRoom {
  return {
    units: 2,
    binding: "per-trade risk",
    bpAfterPct: 0.35,
    bpCapPct: 0.3,
    bpLevel: "over",
    thetaAfterPct: 0.002,
    thetaCeilingPct: 0.003,
    thetaOver: false,
    ...over,
  };
}

function income(over: Partial<ManageCandidates> = {}): ManageCandidates {
  return {
    sourceSession: "2026-10-19",
    takenAt: "2026-10-19T18:45:00+00:00",
    accountBasis: "cash",
    names: 458,
    excluded: { "earnings inside the trade": 120 },
    fits: [candidate()],
    fitsTotal: 1,
    tooBig: [],
    tooBigTotal: 0,
    waiting: [],
    waitingTotal: 0,
    bpUsedPct: 0.25,
    bpCapPct: 0.3,
    bpWarnOver: 0.1,
    error: null,
    caps: { perTradeRiskPct: 0.03, notionalPerNamePct: 0.25, maxNameThetaShare: 0.25 },
    exEarnings: null,
    ...over,
  };
}

describe("IncomeCard", () => {
  it("badges a candidate on a declared daily-reset fund", () => {
    const html = render(<IncomeCard c={income({ fits: [candidate({ underlying: "TSLL", leveraged: "leveraged" }), candidate()] })} />);
    expect(html).toMatch(/TSLL<span class="chip"[^>]*>leveraged</);
    expect(html.match(/>leveraged</g)?.length).toBe(1);
  });

  it("shows each spread's odds, says it has no tested edge, and lists by symbol", () => {
    const odds = { pLoss: 0.2, pMaxLoss: 0.11, breakEven: 23.66, entryCost: 4.74, entryCostShare: 0.13 };
    const html = render(
      <IncomeCard c={income({ fits: [candidate({ kind: "put_spread", odds }), candidate({ kind: "short_put" })], fitsTotal: 60 })} />,
    );
    expect(html).toContain("20% / 11% · b/e 23.66 · cost 13%");
    expect(html).toContain("No tested edge.");
    expect(html).toContain("the first 2 of 60 that fit, by symbol");
    expect(html).not.toContain("theta per dollar");
  });

  it("badges a candidate on a name already held on the same expiry", () => {
    const html = render(<IncomeCard c={income({ fits: [candidate({ underlying: "NKE", heldSameExpiry: true }), candidate()] })} />);
    expect(html).toMatch(/NKE<span class="chip"[^>]*>held · same expiry</);
    expect(html.match(/held · same expiry</g)?.length).toBe(1);
  });

  it("shows each fit's whole position in the suite's money layout, in the package's order", () => {
    const unknown = candidate({ underlying: "AAL", kind: "short_put", earningsDate: null, earningsUnknown: true });
    const html = render(<IncomeCard c={income({ fits: [candidate(), unknown] })} />);
    expect(html.indexOf("AAPL")).toBeLessThan(html.indexOf("AAL"));
    expect(html).toContain("2.47 cr"); // the per-share net price, marked
    expect(html).toContain("+$494.00"); // cash: 2 contracts x $247 received
    expect(html).toContain("$506.00"); // risk: 2 x $253
    expect(html).toContain("per-trade risk");
    expect(html).toContain("re-quote before sending");
    expect(html).toContain("120 earnings inside the trade");
    // An earnings date nobody knows is a chip on the name; a known one (after expiry, or the trade
    // would have been dropped) is not shown at all.
    expect(html).toMatch(/AAL<span class="chip chip-warn"[^>]*>earnings unknown</);
    expect(html.match(/earnings unknown</g)?.length).toBe(1);
    const known = render(<IncomeCard c={income({ fits: [candidate()] })} />);
    expect(known).not.toContain("earnings unknown");
    expect(known).not.toContain("2026-12-04");
  });

  it("names a narrowed trade and what stopped the one that did not fit", () => {
    const wide = candidate({
      units: 0,
      legs: [
        { strike: 345, right: "C", quantity: -1, bid: 3.4, ask: 3.6, delta: 0.45 },
        { strike: 365, right: "C", quantity: 1, bid: 0.2, ask: 0.3, delta: 0.12 },
      ],
      alternative: candidate({ alternativeTo: "call_spread", units: 1 }),
    });
    const fromPut = candidate({ kind: "put_spread", alternativeTo: "short_put" });
    const html = render(
      <IncomeCard c={income({ fits: [candidate({ alternativeTo: "call_spread" }), fromPut], tooBig: [wide], tooBigTotal: 1 })} />,
    );
    expect(html).toContain("narrowed to fit"); // a spread narrowed from its own wider self
    expect(html).toContain("narrowed from the short put");
    expect(html).toContain("stopped by <em>per-trade risk</em>");
    expect(html).toContain("narrowed to −345C / +350C: fits 1");
  });

  it("shows a short put's risk as the stress loss its cap was applied to, not its strike", () => {
    const put = candidate({
      underlying: "AAL",
      kind: "short_put",
      units: 1,
      legs: [{ strike: 11, right: "P", quantity: -1, bid: 0.31, ask: 0.35, delta: -0.4 }],
      perUnit: { credit: 0.33, natural: 0.31, cash: 33, maxLoss: 1067, buyingPower: 1067, stressLoss: 251.07, theta: 0.87, bwd: null },
    });
    const html = render(<IncomeCard c={income({ fits: [put] })} />);
    expect(html).toContain("$251.07");
    expect(html.split("$1067.00").length - 1).toBe(1); // the buying power column only, never as risk
  });

  it("states the caps the evaluation's ruleset set for this account, not fixed numbers", () => {
    const margin = render(
      <IncomeCard
        c={income({ accountBasis: "margin", caps: { perTradeRiskPct: 0.02, notionalPerNamePct: 0.5, maxNameThetaShare: 0.2 } })}
      />,
    );
    expect(margin).toContain("one trade&#x27;s risk 2% of net liq");
    expect(margin).toContain("a short put&#x27;s strike 50% of net liq (margin)");
    expect(margin).toContain("no name over 20% of the theta");
    expect(margin).not.toContain("25%");
    // An evaluation with no ruleset embedded names no number rather than a default.
    const bare = render(<IncomeCard c={income({ caps: null })} />);
    expect(bare).toContain("a short put&#x27;s strike of net liq (cash)");
    expect(bare).not.toMatch(/\d+%/);
  });

  it("lists trades waiting for room, sized as if there were room, with a chip graded by the buying power left", () => {
    const amber = candidate({ underlying: "XLF", units: 0, binding: "buying power", ifRoom: room() });
    const red = candidate({
      underlying: "TLT",
      kind: "put_spread",
      units: 0,
      binding: "theta ceiling",
      ifRoom: room({ units: 1, bpAfterPct: 0.452, bpLevel: "far_over", thetaOver: true, thetaAfterPct: 0.0148 }),
    });
    const ownCap = candidate({ underlying: "AMZN", units: 0, binding: "per-trade risk" });
    const html = render(
      <IncomeCard
        c={income({
          fits: [],
          fitsTotal: 0,
          waiting: [amber, red],
          waitingTotal: 2,
          tooBig: [amber, red, ownCap],
          tooBigTotal: 3,
          bpUsedPct: 0.43,
        })}
      />,
    );
    expect(html).toContain("nothing fits today: 2 are waiting for room below");
    expect(html).toContain("waiting for room: 2 stopped only by buying power or the theta ceiling");
    expect(html).toContain("buying power used 43% · cap 30% at today&#x27;s VIX (amber within 10% of it, red past that)");
    expect(html).toContain('class="chip chip-warn" title="over the cap">35%');
    expect(html).toContain('class="chip chip-bad" title="far over the cap">45%');
    expect(html).toContain("θ over");
    expect(html).toContain("+$494.00"); // cash for the 2 contracts it would take, not the 0 it takes today
    // The too-big list keeps only what a cap on the trade itself stops: a waiting row is not listed twice.
    expect(html).toContain("1 more stopped by a cap on the trade itself");
    expect(html).toContain("<strong>AMZN</strong>");
    expect(html).not.toContain("<strong>XLF</strong>");
  });

  it("offers the ex-earnings toggle only where manage wrote a list, disabled with its reason when refused", () => {
    expect(render(<IncomeCard c={income()} />)).not.toContain("ex earnings buying power");
    const list = income({ bpUsedPct: 0.2 });
    const on = render(
      <IncomeCard c={income({ exEarnings: { trades: ["NFLX", "UAL"], unpriced: [], dollars: 595, error: null, list } })} />,
    );
    expect(on).toContain("ex earnings buying power");
    expect(on).not.toMatch(/<input[^>]*disabled/);
    // Off by default: the whole book's list, not the freed one.
    expect(on).not.toContain("treated as free");
    const refused = render(
      <IncomeCard c={income({ exEarnings: { trades: ["TSLA"], unpriced: ["TSLA"], dollars: null, error: "no bounded max loss", list: null } })} />,
    );
    expect(refused).toMatch(/<input[^>]*disabled/);
    expect(refused).toContain("no bounded max loss");
  });

  it("draws no waiting table from an evaluation without one", () => {
    const html = render(<IncomeCard c={income()} />);
    expect(html).not.toContain("waiting for room");
  });

  it("says nothing fits, or why there is no list, rather than drawing an empty table", () => {
    expect(render(<IncomeCard c={income({ fits: [], fitsTotal: 0 })} />)).toContain("nothing fits today");
    expect(render(<IncomeCard c={income({ error: "no income reading yet" })} />)).toContain("no income reading yet");
    expect(render(<IncomeCard c={null} />)).toBe("");
  });
});

describe("risk matrix tone", () => {
  it("leaves a zero cell untinted: it is neither a gain nor a loss", () => {
    const flat = reading({
      scenarios: {
        moves: [-0.1, 0, 0.1],
        ivShiftWeighting: "term",
        matrix: [{ ivShift: 0, pnl: [0, 0.4, -0.3] }],
        graph: { moves: [-0.1, 0, 0.1], curves: [{ horizonDays: 0, pnl: [0, 0, 0] }] },
        leftOut: [],
      },
    });
    const html = render(<RiskMatrixCard p={flat} />);
    expect(html).not.toContain("var(--pos)");
    expect(html).not.toContain("var(--neg)");
  });
});
