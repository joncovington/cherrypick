import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { DailyRiskTiles, dailyRiskFlag, dailyRiskNote, type DailyRisk } from "../src/components/performance/DailyRiskTiles";
import { MeicPerformanceTab } from "../src/pages/Meic/MeicPerformanceTab";
import { Gauge } from "../src/components/summary/kit";
import { PairTable } from "../src/pages/Advisor/AdvisorPage";

/**
 * The views left on the old layout after the shared tabs (2026-11-07): the daily risk ratios say what
 * they are behind the ?, MEIC's own performance view asks its questions with the trends in a lightbox,
 * a gauge's right tick stays inside the drawing, and the advisor's verdict table keeps an arm on one line.
 */

const text = (el: ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

const risk = (over: Partial<DailyRisk> = {}): DailyRisk => ({
  sharpe: 1.2,
  sortino: 1.5,
  calmar: 0.8,
  recoveryFactor: 2,
  sampleSize: 12,
  undersampledFlag: false,
  sharpeOverfitFlag: false,
  ...over,
});

describe("the daily risk ratios", () => {
  it("draw the tiles only: the annualisation and the warnings are the card's ?", () => {
    const tiles = text(<DailyRiskTiles risk={risk({ undersampledFlag: true })} maxDrawdown={100} />);
    expect(tiles).toContain("sharpe (daily, ann.)");
    expect(tiles).not.toContain("Annualised on 252");
    expect(tiles).not.toContain("Too few sessions");
    const note = text(<>{dailyRiskNote(risk({ undersampledFlag: true }))}</>);
    expect(note).toContain("Annualised on 252 sessions from 12 of them");
    expect(note).toContain("help-warn");
  });

  it("flag a thin sample first, then a Sharpe too good for its sample, else nothing", () => {
    expect(dailyRiskFlag(risk())).toBeUndefined();
    expect(dailyRiskFlag(risk({ sharpeOverfitFlag: true }))).toBe("sharpe on a small sample");
    expect(dailyRiskFlag(risk({ undersampledFlag: true, sharpeOverfitFlag: true }))).toBe("too few sessions");
    expect(text(<>{dailyRiskNote(risk())}</>)).not.toContain("help-warn");
  });
});

const perf = {
  arms: [{ arm: "control", trades: 20, sessions: 5, grossPnl: 300, fees: 40, netPnl: 260, winRatePct: 60, expectancy: 13, profitFactor: 1.4, maxDrawdown: 90 }],
  equity: [{ date: "2026-11-02", netPnl: 50, equity: 100050, drawdown: 0 }],
  risk: risk(),
  periods: [{ period: "2026-11-02", trades: 4, netPnl: 50, cumulative: 50, winRatePct: 75, profitFactor: 2, avgWin: 30, avgLoss: -20, expectancy: 12.5 }],
  studyArms: [],
  bySession: [],
  byIvRank: [],
  regimeCoverage: [{ dimension: "gex", tagged: 20, untagged: 0, coveragePct: 100, degenerate: false }],
};

function meicPerf(): string {
  const client = new QueryClient();
  client.setQueryData(["meic-performance", "paper", "daily", null, null, null], perf);
  return text(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <MeicPerformanceTab mode="paper" symbol={null} profile={null} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MEIC's own performance view", () => {
  it("asks its questions, apart from the shared view's above it", () => {
    const out = meicPerf();
    for (const q of ["How do the arms compare, risk-adjusted?", "How did it build up, period by period?", "When does it do better?"]) expect(out).toContain(q);
    expect(out).not.toContain("How has each arm done?");
    expect(out).toContain("1-lot samples");
    expect(out).toContain("ignores the header&#x27;s scope");
  });

  it("keeps the trend charts in a lightbox, fees as a cost, and no caption in the cards", () => {
    const out = meicPerf();
    expect(out).toContain("win rate, expectancy, wins and losses, trades");
    expect(out).not.toContain("expectancy per trade");
    expect(out).toContain("-$40.00");
    expect(out).not.toContain("Annualised on 252");
    expect(out).not.toContain("not a sized book");
  });
});

describe("a gauge", () => {
  it("ends its right tick at the arc's foot, so a three-digit bound stays inside the drawing", () => {
    const out = text(<Gauge title="completion" rawPct={88} min={0} max={100} caption="of legged entries" />);
    expect(out).toMatch(/<text x="172" y="100" text-anchor="end" class="ts-tick">\+100<\/text>/);
  });
});

describe("the advisor's verdict table", () => {
  it("keeps an arm's name on one line and lets the table scroll", () => {
    const pair = { advisedTag: "advised:abc", baseTag: "control", advised: null, base: null, delta: {}, qualification: {}, underpowered: true } as never;
    const out = text(<PairTable pairs={[pair]} />);
    expect(out).toContain('class="table-scroll"');
    expect(out).not.toContain("data-table-labelled");
  });
});
