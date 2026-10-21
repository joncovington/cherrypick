import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTrade } from "@console/shared";
import { TradesCard } from "../src/pages/Live/ManagePage";

/**
 * The held-for-call chip and "waits for call" are packages/manage's `earnings_trade` verdict, read and
 * never re-derived. A name excused from the rule (`positions.<SYM>.disable: ["earnings_trade"]`) has
 * its ordinary stop and take profit restored by the evaluator, which therefore writes no
 * `earnings_trade` for it -- so the row shows the levels that will actually fire, not a wait.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function trade(over: Partial<ManageTrade>): ManageTrade {
  return {
    tradeId: "XYZ",
    underlying: "XYZ",
    spot: 190,
    kind: "credit",
    shape: null,
    intent: null,
    note: null,
    cost: null,
    value: null,
    pnlGross: -57,
    pnlNet: null,
    fees: null,
    basis: "order_chain",
    chain: null,
    capture: -0.2,
    nearestShortExpiry: "2026-10-23",
    nearestShortDte: 5,
    action: "HOLD",
    confidence: 50,
    rule: "hold",
    reason: "no rule fired",
    explanation: null,
    agreeing: [],
    conflicts: [],
    replaces: [],
    roll: null,
    fired: [],
    legs: [],
    opening: null,
    order: null,
    takeProfit: { closeValue: 140, netPrice: 1.4, netSide: "db" },
    stop: { closeValue: 560, netPrice: 5.6, netSide: "db" },
    timeExit: "2026-10-02",
    earnings: { date: "2026-10-20", days: 2, timeOfDay: "AMC", beforeNearestShortExpiry: true },
    exDividend: null,
    profile: null,
    earningsTrade: null,
    heldForEarnings: [],
    wheel: null,
    pmcc: null,
    ...over,
  };
}

function hold(t: ManageTrade): string {
  return text(renderToString(<TradesCard title="holding" trades={[t]} loading={false} empty="none" variant="hold" />));
}

describe("the held-for-call chip on the positions table", () => {
  it("an earnings trade before its call waits for the call", () => {
    const html = hold(trade({ earningsTrade: { report: "2026-10-20", timeOfDay: "AMC", leadDays: 4, passed: false } }));
    expect(html).toContain("waits for call");
    expect(html).toContain("after call");
    expect(html).toContain("earnings");
  });

  it("a name excused from the rule shows its own stop and take profit, and no chip", () => {
    // The evaluation of an excused name carries an upcoming report (`earnings`) but no `earnings_trade`.
    const html = hold(trade({}));
    expect(html).not.toContain("waits for call");
    expect(html).not.toContain("after call");
    expect(html).toContain("5.60");
    expect(html).toContain("1.40");
  });
});
