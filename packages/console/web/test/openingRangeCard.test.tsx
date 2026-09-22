import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import type { OpeningRangePayload } from "@console/shared";

/**
 * The opening-range card. Two contracts are pinned: an incomplete window says so and shows no
 * range (a zero there would be the most dangerous number on the page), and the card carries no
 * classified measure at all — no ATR, no efficiency, no regime — because those definitions live
 * once, in `cherrypick.core.openingrange`.
 */

const COMPLETE: OpeningRangePayload = {
  session: "2026-09-21",
  symbol: "SPX",
  complete: true,
  reason: null,
  bucketsPresent: 6,
  bucketsExpected: 6,
  high: 7781.73,
  low: 7769.6,
  rangePoints: 12.13,
  first: 7770.0,
  last: 7779.5,
  buckets: [570, 575, 580, 585, 590, 595].map((minute, i) => ({
    minute,
    open: 7770 + i,
    high: 7772 + i,
    low: 7769 + i,
    close: 7771 + i,
    ticks: 60,
  })),
};

const INCOMPLETE: OpeningRangePayload = {
  ...COMPLETE,
  session: "2026-08-17",
  complete: false,
  reason: "window has 5 of 6 buckets",
  bucketsPresent: 5,
  high: null,
  low: null,
  rangePoints: null,
  first: null,
  last: null,
  buckets: COMPLETE.buckets.slice(0, 5),
};

function render(payload: OpeningRangePayload) {
  vi.resetModules();
  vi.doMock("../src/lib/api", () => ({
    useOpeningRange: () => ({ data: payload, isLoading: false, isError: false, dataUpdatedAt: 1 }),
  }));
  return import("../src/pages/Flies/OpeningRangeCard").then(({ OpeningRangeCard }) =>
    renderToString(<OpeningRangeCard filter={{ date: payload.session }} />).replace(/<!--\s*-->/g, ""),
  );
}

describe("OpeningRangeCard", () => {
  it("shows the range, the extremes and a point per bucket", async () => {
    const html = await render(COMPLETE);
    expect(html).toContain("12.13 pts");
    expect(html).toContain("7781.73");
    expect(html).toContain("7769.60");
    expect(html).toContain("09:30–10:00 ET on 2026-09-21");
    expect((html.match(/<circle/g) ?? []).length).toBe(6);
  });

  it("renders an incomplete window as incomplete, never as a zero range", async () => {
    const html = await render(INCOMPLETE);
    expect(html).toContain("incomplete window");
    expect(html).toContain("5 of 6");
    expect(html).not.toContain("pts");
    expect(html).not.toContain("<circle");
  });

  it("displays only the four raw measures, by contract", async () => {
    // The labels ARE the contract: anything with a tunable definition (ATR normalisation, the
    // efficiency ratio, a regime label) lives once in core.openingrange and must not appear as a
    // value here. Checked on the stat labels rather than the whole document, because the card's
    // own footer explains in prose that it carries no ATR.
    const html = await render(COMPLETE);
    const labels = [...html.matchAll(/class="stat-label">([^<]+)</g)].map((m) => m[1]);
    expect(labels).toEqual(["range", "high", "low", "09:55 close"]);
  });
});
