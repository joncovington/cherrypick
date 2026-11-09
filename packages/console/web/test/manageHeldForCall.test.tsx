import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTrade } from "@console/shared";
import { MemoryRouter } from "react-router-dom";
import { TradeDetail } from "../src/pages/Live/ManagePage";
import { PositionCard, PositionCards, SpotLine, chainLegs, chainSteps, idChips, sessionClose, sortByPnl } from "../src/pages/Live/PositionCards";

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
    tradeIdProvisional: false,
    mergedIds: [],
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

// The exit levels left the holding row on 2026-10-30: they are
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
    tradeIdProvisional: false,
    mergedIds: [],
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

  it("shows the underlying's live price with its change on the day, or the evaluation's spot muted with none", () => {
    const live = renderToString(<SpotLine q={{ last: 51.5, prevClose: 50, source: "dxlink", direction: "up", ts: 1 }} evaluated={49} />);
    expect(text(live)).toBe("51.50 +1.50 +3.00%");
    expect(live).toContain('class="pnl-pos"');
    expect(text(renderToString(<SpotLine q={{ last: 48, prevClose: 50, source: "dxlink", direction: "down", ts: 1 }} evaluated={49} />))).toBe("48.00 −2.00 −4.00%");
    // A cached price (the live feed down) shows no change between numbers from two sources.
    const cached = renderToString(<SpotLine q={{ last: 51.5, prevClose: 50, source: "cache", direction: null, ts: 1 }} evaluated={49} />);
    expect(text(cached)).toBe("49.00");
    expect(cached).toContain("muted");
    expect(renderToString(<SpotLine q={undefined} evaluated={null} />)).toBe("");
  });

  it("out of the session shows the last session's close from its daily candle, with its change on the one before", () => {
    // Daily bars start at midnight UTC of their trading date.
    const day = (d: string, c: number) => ({ t: Date.parse(`${d}T00:00:00Z`) / 1000, o: c, h: c, l: c, c, v: 0 });
    const bars = [day("2026-11-02", 98), day("2026-11-03", 100), day("2026-11-04", 102.5)];
    // 01:30 ET on the 5th, before the open: the 4th is the last session.
    const pre = sessionClose([...bars, day("2026-11-05", 103)], new Date("2026-11-05T06:30:00Z"));
    expect(pre).toEqual({ date: "2026-11-04", close: 102.5, change: 2.5, changePct: 2.5 });
    // 17:00 ET on the 4th, after the close: today's bar is the session just ended.
    expect(sessionClose(bars, new Date("2026-11-04T22:00:00Z"))?.date).toBe("2026-11-04");
    // 08:00 ET on the 4th: the 4th's pre-market bar is not a session; the 3rd is.
    expect(sessionClose(bars, new Date("2026-11-04T13:00:00Z"))).toEqual({ date: "2026-11-03", close: 100, change: 2, changePct: (2 / 98) * 100 });
    expect(sessionClose([day("2026-11-04", 102.5)], new Date("2026-11-05T06:30:00Z"))).toEqual({ date: "2026-11-04", close: 102.5, change: null, changePct: null });
    expect(sessionClose([], new Date())).toBeNull();
    // The closed line wins over a live after-hours price, and says it is the close.
    const live = { last: 101, prevClose: 102.5, source: "dxlink" as const, direction: "down" as const, ts: 1 };
    expect(text(renderToString(<SpotLine q={live} evaluated={99} closed={pre} />))).toBe("102.50 +2.50 +2.50% close");
  });

  it("puts each leg on its own line, a buy green and a sell red, the side that set the net first", () => {
    // A debit opens with its buys on top, a credit with its sells, the broker's order kept within a side.
    const debit = chainLegs(["STO 1 2026-12-18 106C @ 0.80", "BTO 1 2028-12-15 100C @ 9.00"], "db");
    expect(debit.map((l) => l.action)).toEqual(["BTO", "STO"]);
    expect(debit[0]).toEqual({ action: "BTO", buy: true, text: "1 2028-12-15 100C @ 9.00" });
    expect(chainLegs(["BTO 1 90P", "STO 1 95P", "BTO 1 110C", "STO 1 105C"], "cr").map((l) => l.text)).toEqual(["1 95P", "1 105C", "1 90P", "1 110C"]);
    expect(chainSteps(rolled)[1].legs.map((l) => l.action)).toEqual(["STO", "BTC"]);
    // A line with no action keeps its text and gets no chip.
    expect(chainLegs(["exercise 1 100C"], "db")).toEqual([{ action: null, buy: null, text: "exercise 1 100C" }]);
    const html = renderToString(<PositionCard t={rolled} onOpen={() => undefined} />);
    expect(html).toContain('class="pos-leg-act is-sell">STO');
    expect(html).toContain('class="pos-leg-act is-buy">BTC');
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

  it("a trade's ID says when it is provisional or the survivor of a join, and nothing when settled", () => {
    const chips = (t: ManageTrade) => renderToString(<>{idChips(t)}</>);
    expect(text(chips(trade({})))).toBe("");
    expect(text(chips(trade({ tradeIdProvisional: true })))).toBe("provisional ID");
    expect(text(chips(trade({ mergedIds: ["XYZ-260925-1"] })))).toBe("merged XYZ-260925-1");
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
    // The sort is behind the filter & sort button; the summary line says it, and nothing is counted changed.
    expect(desc).toMatch(/by P&(amp;)?L, high to low/);
    expect(desc).not.toContain("Filter and sort, 1 changed");
    const dte = board("/live/positions?sort=dte&rev=1");
    expect(dte).toContain("by days to the nearest short, reversed");
    // An older link's pnl=asc still reads as P&L reversed.
    const asc = board("/live/positions?pnl=asc");
    expect(asc.indexOf("-$20.00")).toBeLessThan(asc.indexOf("185.20"));
    const act = board("/live/positions?show=act");
    expect(act).not.toContain("$50.00");
  });
});
