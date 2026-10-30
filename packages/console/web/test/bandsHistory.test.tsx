import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageBand, ManageBandsSession } from "@console/shared";
import { BandsHistoryView } from "../src/pages/Live/BandsHistory";

/**
 * The bands over time (docs/console-review-plan.md batch 5). Each point is placed against the band
 * that session's evaluation stated, and "inside" is manage's flag, never worked out here: a value the
 * page could place inside its bounds still reads as outside when manage said so.
 */

const band = (value: number, inside: boolean, low = -10, high = 10): ManageBand => ({ low, high, value, inside });

const session = (day: string, delta: ManageBand): ManageBandsSession => ({
  session: `2026-10-${day}`,
  netLiq: 14000,
  theta: 25,
  delta,
  thetaPct: band(0.002, true, 0.001, 0.003),
  buyingPower: null,
});

function render(sessions: ManageBandsSession[]): string {
  return renderToString(<BandsHistoryView sessions={sessions} />).replace(/<!-- -->/g, "");
}

describe("BandsHistoryView", () => {
  it("counts sessions inside by manage's flag and marks the outside points", () => {
    // 10-20's value sits inside its bounds, but manage judged it outside: the page follows manage.
    const html = render([session("19", band(3, true)), session("20", band(4, false)), session("21", band(15, false))]);
    expect(html).toContain("beta-weighted delta");
    expect(html).toContain("inside 1 of 3 sessions");
    expect(html.match(/data-out="1"/g)).toHaveLength(2);
    expect(html).toContain("10-19 to 10-21");
  });

  it("draws no strip for a band no session carried", () => {
    const html = render([session("19", band(3, true)), session("20", band(4, true))]);
    expect(html).not.toContain("buying power used");
  });

  it("says how many evaluations it has rather than drawing one point as a trend", () => {
    const html = render([session("19", band(3, true))]);
    expect(html).toContain("needs two evaluations with bands; 1 so far");
    expect(html).not.toContain("<svg");
  });
});
