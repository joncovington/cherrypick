import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import type { ManageLivePayload, ManageLiveTrade } from "@console/shared";
import { LiveCaptureCell, LiveMarksContext, LivePnlCell, LiveStatus, liveMarks } from "../src/pages/Live/LiveMarks";

/**
 * The live columns show the stream's re-mark of the newest evaluation and judge nothing: a dash for a
 * trade any leg of which is unquoted, net where the evaluation had fees, and a chip when the live value
 * reaches the evaluation's own take-profit or stop level.
 */

function trade(over: Partial<ManageLiveTrade> = {}): ManageLiveTrade {
  return { tradeId: "GLW", value: -240, pnlGross: 560, pnlNet: 556, capture: 0.7, atTakeProfit: true, atStop: false, legsQuoted: 3, legsTotal: 3, ...over };
}

function payload(trades: ManageLiveTrade[]): ManageLivePayload {
  return { session: "2026-10-22", oldestQuoteAt: Date.now() - 4000, maxAgeSeconds: 120, trades, accounts: [], spots: {} };
}

function render(p: ManageLivePayload | null, el: ReactElement): string {
  return renderToString(
    <LiveMarksContext.Provider value={p ? liveMarks(p) : null}>
      <table>
        <tbody>
          <tr>{el}</tr>
        </tbody>
      </table>
      <LiveStatus />
    </LiveMarksContext.Provider>,
  ).replace(/<!-- -->/g, "");
}

describe("live marks", () => {
  it("shows net P&L, the live capture and the take-profit chip", () => {
    const p = payload([trade()]);
    expect(render(p, <LivePnlCell id="GLW" />)).toContain("$556");
    const cap = render(p, <LiveCaptureCell id="GLW" />);
    expect(cap).toContain("70%");
    expect(cap).toContain("at TP");
    expect(cap).not.toContain("at stop");
  });

  it("dashes a part-quoted trade and says why", () => {
    const html = render(payload([trade({ value: null, pnlGross: null, pnlNet: null, capture: null, atTakeProfit: false, legsQuoted: 1 })]), <LivePnlCell id="GLW" />);
    expect(html).toContain("—");
    expect(html).toContain("1 of 3 legs have a fresh quote");
  });

  it("says when nothing is streaming, and shows nothing off the newest evaluation", () => {
    expect(render(payload([trade({ pnlGross: null })]), <LivePnlCell id="GLW" />)).toContain("live marks: none streaming");
    expect(render(null, <LivePnlCell id="GLW" />)).not.toContain("live marks:");
  });
});
