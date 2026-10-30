import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTrade } from "@console/shared";
import { TradeDetail, TradesCard } from "../src/pages/Live/ManagePage";
import { aligned, inView, tables } from "./viewTables";

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

// The exit levels left the holding row on 2026-10-30 (docs/console-review-plan.md batch 3): they are
// the expanded line's "exit levels" card, so the held-for-call wording is read there.
function expanded(t: ManageTrade): string {
  return text(renderToString(<TradeDetail t={t} />));
}

describe("the held-for-call chip on the positions table", () => {
  it("an earnings trade before its call waits for the call", () => {
    const t = trade({ earningsTrade: { report: "2026-10-20", timeOfDay: "AMC", leadDays: 4, passed: false } });
    expect(hold(t)).toContain("earnings"); // the chip, in the trade cell
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

/** Live → positions in the simple and detail views (2026-10-21). */
describe("the positions tables, simple and detail", () => {
  const card = (variant: "act" | "hold", t: ManageTrade) =>
    renderToString(<TradesCard title={variant} trades={[t]} loading={false} empty="none" variant={variant} />);
  const act = trade({ action: "CLOSE", reason: "stop hit", pnlGross: -57, pnlNet: -59.6, fees: -2.6 });

  it("keeps conf., kind and the exit levels out of the row in both views, and every row stays under its headers", () => {
    for (const variant of ["act", "hold"] as const) {
      const simple = tables(inView("simple", () => card(variant, act)))[0]!;
      const detail = tables(inView("detail", () => card(variant, act)))[0]!;
      for (const t of [simple, detail]) {
        expect(t.headers).not.toContain("conf.");
        expect(t.headers).not.toContain("kind");
        expect(t.headers).not.toContain("live capture");
        expect(t.headers).not.toContain("take profit");
      }
      expect(aligned(simple)).toBe(true);
      expect(aligned(detail)).toBe(true);
    }
  });

  it("the holding table has no action column in either view; action needed keeps it", () => {
    for (const view of ["simple", "detail"] as const) {
      expect(tables(inView(view, () => card("hold", trade({}))))[0]!.headers).not.toContain("action");
      expect(tables(inView(view, () => card("act", act)))[0]!.headers).toContain("action");
    }
  });

  it("a settling trade in the holding table still says so, in its trade cell", () => {
    expect(text(card("hold", trade({ action: "SETTLES" })))).toContain("settles");
  });

  it("shows P&L net of fees in both views, and a dash (never the gross) where fees were not read", () => {
    for (const view of ["simple", "detail"] as const) {
      const html = inView(view, () => card("act", act));
      expect(tables(html)[0]!.headers).toContain("P&L (net)");
      expect(tables(html)[0]!.headers).not.toContain("mark P&L (gross)");
      expect(text(html)).toContain("-$59.60");
    }
    const unread = text(card("hold", trade({ pnlGross: -57, pnlNet: null })));
    expect(unread).not.toContain("-$57.00");
  });
});
