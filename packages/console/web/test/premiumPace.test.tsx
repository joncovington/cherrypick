import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageBand, ManagePremium } from "@console/shared";
import { PremiumPaceCard } from "../src/pages/Live/PremiumPace";

/**
 * The premium pace (docs/console-review-plan.md batch 5): theta against manage's target, and
 * collected beside kept per month. Every figure is manage's; the foot row adds up the months above
 * it, and what manage left out of `kept` is stated, never silently dropped.
 */

const band: ManageBand = { low: 0.001, high: 0.003, value: 0.0019, inside: true };

const premium = (over: Partial<ManagePremium> = {}): ManagePremium => ({
  target: { pctNlv: 0.002, dollars: 20, theta: 19, ratio: 0.95 },
  error: null,
  historyFrom: "2025-09-25",
  months: [
    { month: "2026-09", collected: 400, orders: 2, kept: 120.5, closed: 4 },
    { month: "2026-10", collected: 600, orders: 3, kept: -80.25, closed: 6 },
  ],
  week: { from: "2026-10-26", collected: 150, orders: 1, kept: -10, closed: 2 },
  unmatched: { legs: 0, net: 0 },
  zeroDte: { legs: 3, net: -45.5, underlyings: ["SPX"] },
  ...over,
});

function render(p: ManagePremium | null): string {
  return renderToString(<PremiumPaceCard premium={p} thetaBand={band} />)
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

describe("PremiumPaceCard", () => {
  it("states theta against manage's target", () => {
    const html = render(premium());
    expect(html).toContain("theta $19.00 a day against a target of $20.00 (0.20% of net liq)");
    expect(html).toContain("95% of target");
  });

  it("lists each month and foots the months to their totals", () => {
    const html = render(premium());
    expect(html).toContain('data-month="2026-09"');
    expect(html).toContain("Oct 26");
    expect(html).toContain("2 months");
    expect(html).toContain("$1000.00"); // 400 + 600 collected
    expect(html).toContain("$40.25"); // 120.50 - 80.25 kept
    expect(html).toContain(">10<"); // legs closed
  });

  it("states what was left out of kept, and omits an exclusion with nothing in it", () => {
    const html = render(premium());
    expect(html).toContain("3 0DTE legs on SPX (the live loops' own, -$45.50)");
    expect(html).not.toContain("opened before the history");
    expect(render(premium({ unmatched: { legs: 1, net: -12 } }))).toContain("1 leg opened before the history");
  });

  it("keeps the target and says why when there is no history to count", () => {
    const html = render(premium({ error: "no account-wide history in this snapshot", months: [], week: null }));
    expect(html).toContain("collected and kept: no account-wide history in this snapshot");
    expect(html).toContain("target of $20.00");
    expect(html).not.toContain("<table");
  });

  it("draws nothing for an evaluation written before the pace existed", () => {
    expect(render(null)).toBe("");
  });
});
