import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTrade } from "@console/shared";
import { MemoryRouter } from "react-router-dom";
import { TradeDetail } from "../src/pages/Live/ManagePage";
import { PositionCard, PositionCards, chainSteps, sortByPnl } from "../src/pages/Live/PositionCards";

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
  return text(renderToString(<PositionCard t={t} onOpen={() => undefined} />));
}

// The exit levels left the holding row on 2026-10-30 (docs/console-review-plan.md batch 3): they are
// the expanded line's "exit levels" card, so the held-for-call wording is read there.
function expanded(t: ManageTrade): string {
  return text(renderToString(<TradeDetail t={t} />));
}

describe("the held-for-call chip on the positions cards", () => {
  it("an earnings trade before its call waits for the call", () => {
    const t = trade({ earningsTrade: { report: "2026-10-20", timeOfDay: "AMC", leadDays: 4, passed: false } });
    expect(hold(t)).toContain("earnings"); // the chip, on the card
    expect(expanded(t)).toContain("waits for the call");
    expect(expanded(t)).toContain("after the call");
  });

  it("a name excused from the rule shows its own stop and take profit, and no chip", () => {
    // The evaluation of an excused name carries an upcoming report (`earnings`) but no `earnings_trade`.
    const html = expanded(trade({}));
    expect(html).not.toContain("waits for the call");
    expect(html).not.toContain("after the call");
    expect(html).toContain("5.60");
    expect(html).toContain("1.40");
  });
});

/** Live → positions as cards (2026-11-04): a card per trade, its order chain down the card. */
describe("the positions cards", () => {
  const order = (date: string, role: string, netV: number | null, netPrice: number, netSide: "cr" | "db", legs: string[]) => ({
    date,
    role,
    net: netV,
    netPrice,
    netSide,
    fees: -1.3,
    legs,
  });
  const rolled = trade({
    tradeId: "IWM",
    action: "TAKE_PROFIT",
    pnlGross: 187,
    pnlNet: 185.2,
    fees: -1.8,
    value: -88,
    chain: {
      orders: [order("2026-10-02", "open", 210, 2.1, "cr", ["STO 1 IWM 215P"]), order("2026-10-24", "roll", 65, 0.65, "cr", ["BTC 1 IWM 215P", "STO 1 IWM 210P"])],
      cash: 275,
      fees: -1.8,
      rolls: 1,
    },
  });

  it("draws the chain in order, the first order named open, each with the chain's running total", () => {
    const steps = chainSteps(rolled);
    expect(steps.map((x) => x.role)).toEqual(["open", "roll"]);
    expect(steps.map((x) => x.price)).toEqual(["2.10 cr", "0.65 cr"]);
    expect(steps.map((x) => x.total)).toEqual([210, 275]);
    const html = text(renderToString(<PositionCard t={rolled} onOpen={() => undefined} />));
    expect(html).toContain("1 roll");
    expect(html).toContain("close at mid -$88.00");
    expect(html).toContain("taken in +$275.00");
  });

  it("stops the running total at an order whose cash is unknown, and falls back to the opening with no chain", () => {
    const gap = trade({ chain: { orders: [order("2026-10-01", "open", 100, 1, "cr", []), order("2026-10-05", "roll", null, 0.2, "cr", []), order("2026-10-09", "roll", 30, 0.3, "cr", [])], cash: null, fees: null, rolls: 2 } });
    expect(chainSteps(gap).map((x) => x.total)).toEqual([100, null, null]);
    const legsOnly = trade({ basis: "open_legs", chain: null, opening: { net: 120, netPrice: 1.2, netSide: "cr" } });
    expect(chainSteps(legsOnly)).toHaveLength(1);
    expect(text(renderToString(<PositionCard t={legsOnly} onOpen={() => undefined} />))).toContain("no reconciled chain");
  });

  it("shows P&L net of fees, never the gross where fees were not read, and marks a losing card red", () => {
    const loss = renderToString(<PositionCard t={trade({ action: "CLOSE", pnlGross: -57, pnlNet: -59.6, fees: -2.6 })} onOpen={() => undefined} />);
    expect(text(loss)).toContain("-$59.60");
    expect(loss).toContain("pos-card is-neg");
    expect(loss).toContain("pnl-neg");
    const unread = renderToString(<PositionCard t={trade({ pnlGross: -57, pnlNet: null })} onOpen={() => undefined} />);
    expect(text(unread)).not.toContain("-$57.00");
    expect(unread).not.toContain("pos-card is-neg");
  });

  it("a settling trade says so on its card", () => {
    expect(hold(trade({ action: "SETTLES" }))).toContain("settles");
  });

  it("sorts by P&L either way, a trade with no net last both times", () => {
    const a = trade({ tradeId: "A", pnlNet: 50 });
    const b = trade({ tradeId: "B", pnlNet: -20 });
    const c = trade({ tradeId: "C", pnlNet: null });
    const d = trade({ tradeId: "D", pnlNet: 120 });
    expect(sortByPnl([a, b, c, d], "desc").map((t) => t.tradeId)).toEqual(["D", "A", "B", "C"]);
    expect(sortByPnl([a, b, c, d], "asc").map((t) => t.tradeId)).toEqual(["B", "A", "D", "C"]);
  });

  it("the board counts what needs action and what rolled, and orders by the URL's choice", () => {
    const trades = [trade({ tradeId: "A", pnlNet: 50 }), rolled, trade({ tradeId: "B", action: "CLOSE", pnlNet: -20 })];
    const board = (url: string) =>
      text(
        renderToString(
          <MemoryRouter initialEntries={[url]}>
            <PositionCards trades={trades} loading={false} detail={() => null} />
          </MemoryRouter>,
        ),
      );
    const desc = board("/live/positions");
    expect(desc).toContain("all 3");
    expect(desc).toContain("needs action 2");
    expect(desc).toContain("rolled 1");
    expect(desc.indexOf("185.20")).toBeLessThan(desc.indexOf("-$20.00"));
    const asc = board("/live/positions?pnl=asc");
    expect(asc.indexOf("-$20.00")).toBeLessThan(asc.indexOf("185.20"));
    const act = board("/live/positions?show=act");
    expect(act).not.toContain("$50.00");
  });
});
