import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import type { ManagePortfolio } from "@console/shared";
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
      earnings: { trades: ["RTX", "META"], theta: 35, thetaShare: 0.724, downside: -4292.5 },
    },
    guidance: [{ rule: "portfolio.theta", confidence: 40, reason: "above the ceiling", drivers: [{ underlying: "GOOGL", value: 9.06 }] }],
    underlyings: [
      { underlying: "SPY", beta: 1, bwd: -9.13, theta: 5.93, vega30: 9.84, bwdShare: 0.164, thetaShare: 0.128, betaDoubt: null },
      { underlying: "META", beta: 1.6, bwd: 6.22, theta: 6.42, vega30: -10.06, bwdShare: 0.112, thetaShare: 0.138, betaDoubt: null },
      { underlying: "FXI", beta: 0.27, bwd: -0.61, theta: -1.34, vega30: 3.93, bwdShare: 0.011, thetaShare: 0.029, betaDoubt: "opposite signs" },
    ],
    hedges: null,
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

  it("shows each band's chip from the reading and the guidance it fired", () => {
    const html = render(<PortfolioCards p={reading()} />);
    expect(html).toContain("in band"); // delta
    expect(html).toContain("outside band"); // theta
    expect(html).toContain("portfolio.theta");
    expect(html).toContain("SPY −10%");
    expect(html).toContain("Traditional IRA");
  });

  it("draws long and short delta without a sign tone", () => {
    const html = render(<PortfolioCards p={reading()} />);
    const delta = html.slice(html.indexOf("where the delta comes from"), html.indexOf("where the theta comes from"));
    expect(delta).toContain("diverge-neutral");
    expect(delta).not.toContain("pnl-neg");
    expect(delta).toContain("FXI ⚠");
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
    expect(render(<LadderCard ladder={null} />)).toBe("");
  });

  it("draws nothing it has no reading for", () => {
    const html = render(<ScenarioCards p={reading({ scenarios: null, ladder: null })} />);
    expect(html).not.toContain("risk matrix");
    expect(html).not.toContain("risk graph");
    expect(html).toContain("buying power");
  });
});
