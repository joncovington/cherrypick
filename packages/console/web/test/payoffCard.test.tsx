import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManagePayoff, ManagePayoffName } from "@console/shared";
import { PayoffStrip, chartGeometry, legText } from "../src/pages/Live/PayoffCard";

/**
 * Live → wheel's payoff draws manage's `payoff`: the strip shows each name's P&L now and breakevens as
 * manage wrote them, and the chart scales its curves to fit with zero always in view. Synthetic
 * names and figures.
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

function name(over: Partial<ManagePayoffName> = {}): ManagePayoffName {
  const grid = [40, 45, 50, 55, 60];
  return {
    underlying: "AAA",
    spot: 50,
    model: "american",
    divYield: null,
    yieldSource: "no dividend record: none assumed",
    why: null,
    rate: 0.04,
    cost: -120,
    pnlNow: 30,
    grid,
    curves: [
      { label: "today", date: "2026-11-04", days: 0, expiry: false, pnl: [-700, -260, 20, 70, 100] },
      { label: "at expiry 2026-12-18", date: "2026-12-18", days: 44, expiry: true, pnl: [-680, -180, 120, 120, 120] },
    ],
    breakevens: [46.8],
    legs: [{ leg: "-1 2026-12-18 48P", quantity: -1, mid: 0.9, iv: 0.29 }],
    ...over,
  };
}

function strip(p: ManagePayoff): string {
  return text(
    renderToString(
      <MemoryRouter initialEntries={["/live/wheel"]}>
        <PayoffStrip payoff={p} />
      </MemoryRouter>,
    ),
  );
}

describe("the payoff strip", () => {
  it("lists each held name with manage's P&L now and breakevens", () => {
    const out = strip({ names: [name(), name({ underlying: "BBB", pnlNow: -117, breakevens: [68.47, 72.1] })] });
    expect(out).toContain("AAA +$30.00 BE 46.80");
    expect(out).toContain("BBB -$117.00 BE 68.47 / 72.10");
  });

  it("draws nothing when no name is held", () => {
    expect(strip({ names: [] })).toBe("");
  });
});

describe("the chart's geometry", () => {
  it("keeps zero in view and spans the curves shown", () => {
    const g = chartGeometry(name(), [0, 1]);
    expect(g.lo).toBe(-700);
    expect(g.hi).toBe(120);
    // A curve all above zero still puts zero on the chart.
    const up = chartGeometry(
      name({ curves: [{ label: "at expiry", date: "", days: 1, expiry: true, pnl: [10, 20, 30, 40, 50] }] }),
      [0],
    );
    expect(up.lo).toBe(0);
    expect(up.path([10, 20, 30, 40, 50]).split("L").length).toBe(5);
  });

  it("leaves a curve manage could not draw out of the scale", () => {
    const g = chartGeometry(
      name({
        curves: [
          { label: "today", date: "", days: 0, expiry: false, pnl: null },
          { label: "at expiry", date: "", days: 44, expiry: true, pnl: [-5, 0, 5, 10, 15] },
        ],
      }),
      [0, 1],
    );
    expect(g.lo).toBe(-5);
    expect(g.hi).toBe(15);
  });

  it("takes the modelled trade's 'before' curve into the scale", () => {
    const g = chartGeometry(name(), [1], [[-2000, 0, 50]]);
    expect(g.lo).toBe(-2000);
  });
});

describe("the model a trade form", () => {
  const leg = { side: "sell" as const, qty: "1", expiry: "2026-12-18", strike: "46", right: "P" as const, price: "" };

  it("writes the leg text manage reads, the price only when given", () => {
    expect(legText(leg)).toBe("-1 2026-12-18 46P");
    expect(legText({ ...leg, side: "buy", qty: "2", strike: "46.5", right: "C", price: "1.05" })).toBe("+2 2026-12-18 46.5C @ 1.05");
  });

  it("is not ready until every field is filled in", () => {
    expect(legText({ ...leg, strike: "" })).toBeNull();
    expect(legText({ ...leg, qty: "0" })).toBeNull();
    expect(legText({ ...leg, expiry: "" })).toBeNull();
    expect(legText({ ...leg, price: "-1" })).toBeNull();
  });
});
