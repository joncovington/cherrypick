import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import type { ManagePortfolio } from "@console/shared";
import { BandDial, PortfolioCards } from "../src/pages/Live/PortfolioCards";

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
            after: { bwd: -0.57, inBand: true, theta: 46.44, thetaPctNetLiq: 0.00325, breakevenPct: 0.0182, buyingPowerUsedPct: 0.558 },
          },
          { kind: "debit", structure: "put debit spread", error: "one spread ties up $158, past the 50% buying-power cap ($0 left)", units: null, legs: [], priceLabel: null, natural: null, cash: null, maxLoss: null, bwdChange: null, thetaChange: null, after: null },
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
});
