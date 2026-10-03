import type { ReactNode } from "react";
import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { OptionsFlowDay } from "@console/shared";
import { FlowSpreads, FlowToday, contractLabel } from "../src/pages/Flow/FlowPage";

/**
 * The Options flow page lays out the capture and decides nothing. What these guard is what the
 * Discord series and a reader depend on: every Today card's title carries the session date and draws
 * an SVG (`ui-check --card` needs both), a derived premium says so, tone follows only the site's word
 * or a spread's direction, and a missing number is an em dash.
 */

const DAY: OptionsFlowDay = {
  session: "2026-10-02",
  savedAt: "2026-10-02T20:52:00+00:00",
  birdseye: [
    {
      symbol: "TSLA",
      name: "Tesla, Inc.",
      buckets: { "1s": 421800 },
      bands: { "1": 421800, "2-10": 288900, "11-99": 48700, "100+": 3040 },
      calls: 462900,
      puts: 299600,
      total: 762500,
      callShare: 0.607,
      shown: { total: "762.5K", "1s": "421.8K" },
    },
  ],
  outrights: [
    {
      symbol: "PCG",
      name: "PG&E Corporation",
      timeEt: "13:38:45.517",
      size: 40900,
      expires: "2027-01-15",
      strike: 16,
      cp: "call",
      price: 0.37,
      premium: 1513300,
      premiumDerived: true,
      side: { sentiment: "Bullish", fill: "On Ask", edge: 1 },
    },
  ],
  sweeps: [
    {
      symbol: "TSLA",
      name: "Tesla, Inc.",
      timeEt: null,
      size: 148,
      expires: "2028-06-16",
      strike: 610,
      cp: "call",
      price: 40.45,
      premium: null,
      premiumDerived: false,
      side: { sentiment: "Bearish", fill: "On Bid", edge: -1 },
    },
  ],
  spreads: [
    {
      symbol: "AI",
      name: "C3.ai, Inc.",
      timeEt: "15:01:34.327",
      size: 41900,
      expires: "2026-10-09",
      type: "CS",
      cp: "call",
      spread: "261009 11/11.5 CS",
      price: -0.22,
      delta: -0.24,
      premium: -921800,
      direction: "sold",
      group: "AI 15:01:34.327 41900",
      underlying: { last: 11.12, bid: 11.11, ask: 11.12 },
      exchange: "EDGX",
    },
  ],
  voloi: [{ symbol: "SPCX", name: null, volume: 135071, oi: 119, vOi: 1135.05, expires: "2026-10-02", strike: 157.5, cp: "put" }],
  openings: [],
  names: [{ symbol: "TSLA", name: "Tesla, Inc.", tables: ["birdseye", "sweeps"] }],
  premiumBySide: { Bullish: 1513300 },
  tradesBySide: { Bullish: 1 },
  largestTrade: { table: "outrights", symbol: "PCG", premium: 1513300 },
};

function render(node: ReactNode): string {
  return renderToString(<MemoryRouter initialEntries={["/flow?session=2026-10-02"]}>{node}</MemoryRouter>);
}

describe("the Options flow page", () => {
  it("every Today card is titled with its session and draws an SVG, so the Discord capture can find it", () => {
    const html = render(<FlowToday day={DAY} />);
    const cards = html
      .split('<section class="gcard')
      .slice(1)
      .filter((c) => !c.includes("stat-big"));
    expect(cards.length).toBe(6);
    for (const card of cards) {
      expect(card).toContain("— 2026-10-02");
      expect(card).toContain("<svg");
    }
  });

  it("marks a derived premium, tones trades only by the site's word, and dashes what did not read", () => {
    const html = render(<FlowToday day={DAY} />);
    expect(html).toContain("$1.51M");
    expect(html).toContain('<span class="muted">*</span>');
    expect(html).toMatch(/class="pnl-pos"[^>]*>bullish/);
    expect(html).toMatch(/class="pnl-neg"[^>]*>bearish/);
    expect(html).toContain("—"); // the sweep's premium did not read
    expect(html).not.toContain("$0");
  });

  it("a spread shows the capture's direction and its link, with the price unsigned", () => {
    const html = render(<FlowSpreads day={DAY} />);
    expect(html).toMatch(/class="pnl-neg"[^>]*>sold/);
    expect(html).toContain("⛓");
    expect(html).toContain(">0.22<");
    expect(html).toContain("bid 11.11 / ask 11.12");
  });

  it("names a contract the way a trader reads it", () => {
    expect(contractLabel("2027-01-15", 16, "call")).toBe("15 Jan 27 16C");
    expect(contractLabel(null, 157.5, "put")).toBe("— 157.5P");
  });
});
