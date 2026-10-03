import type { ReactNode } from "react";
import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { OptionsFlowDay } from "@console/shared";
import { FlowDerived, FlowSpreads, FlowToday, contractLabel } from "../src/pages/Flow/FlowPage";

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
  derived: {
    checks: {
      siteVote: { agrees: 16, neutral: 0, opposite: 0 },
      delta: { broker: 20, singles: 20, off: ["VST 17 Dec 27 195P: broker -0.69, model -0.58"] },
      close: null,
      audit: null,
      review: null,
    },
    scoredAt: "2026-10-02T20:45:00+00:00",
    confirmedAt: null,
    flows: [
      {
        kind: "outright", symbol: "PCG", what: "15 Jan 27 16C", size: 40900, premium: 1513300, direction: "bought",
        view: "bullish", delta: 0.22, deltaDollars: 11_000_000, days: 105, flags: [], score: 46,
        factors: { size: 0.77, conviction: 1, purity: 1, opening: 0.6 }, confirmed: null, confirmedScore: null,
      },
      {
        kind: "spread", symbol: "SMCI", what: "261009 43.5/45.5 CS", size: 13236, premium: 820632, direction: "sold",
        view: "bearish", delta: 0.17, deltaDollars: 9_600_000, days: 7, flags: ["≤7d"], score: -24,
        factors: { size: 0.75, conviction: 0.7, purity: 0.75, opening: 0.6 }, confirmed: "opened", confirmedScore: -40,
      },
    ],
    unread: [],
    names: [
      { symbol: "PCG", flows: 1, bullish: 11_000_000, bearish: 0, net: 11_000_000, unread: 0, top: 46 },
      { symbol: "SMCI", flows: 2, bullish: 0, bearish: 13_300_000, net: -13_300_000, unread: 0, top: -24 },
    ],
  },
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
    expect(cards.length).toBe(8);
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

  it("the derived flow card shows the confirmed score when there is one, toned by the view", () => {
    const html = render(<FlowToday day={DAY} />);
    expect(html).toContain("Derived flow — 2026-10-02");
    expect(html).toMatch(/class="num pnl-neg"[^>]*>-40</); // SMCI confirmed opened: -40, not the first -24
    expect(html).toMatch(/class="num pnl-pos"[^>]*>\+46</);
    expect(html).toContain("Net by name — 2026-10-02");
    const unscored = render(<FlowToday day={{ ...DAY, derived: null }} />);
    expect(unscored).toContain("Not scored yet for this session.");
  });

  it("the derived tab lays out the checks as recorded, and says what has not run yet", () => {
    const html = render(<FlowDerived day={DAY} />);
    expect(html).toContain("16 agree · 0 site neutral · 0 opposite");
    expect(html).toContain("20 of 20 from the broker; the model off by more than 0.10 on 1");
    expect(html).toContain("the next morning");
    expect(html).toContain("none yet");
    expect(html).toContain("not run yet");
  });

  it("names a contract the way a trader reads it", () => {
    expect(contractLabel("2027-01-15", 16, "call")).toBe("15 Jan 27 16C");
    expect(contractLabel(null, 157.5, "put")).toBe("— 157.5P");
  });
});
