import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationConditions, RegimeCuts } from "@console/shared";
import { ConditionsBody, conditionsLead, labelWords } from "../src/pages/Automations/AutomationConditions";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const CUTS = {
  cutVersion: 2,
  module: "automations",
  generatedAt: null,
  session: "2026-11-23",
  symbol: "SPX",
  armColumn: "arm",
  entryModes: null,
  thinBelowSessions: 3,
  minEffectiveN: 14,
  era: { start: null, key: null, boundingBreak: null, breaks: [], ignoredFuture: [], caveats: [] },
  arms: [
    {
      arm: "mine",
      today: {
        trend: { bucket: "downtrend", readable: true, why: null, sessions: 10, trades: 10, netPnl: -50, avgPnl: -5, thin: false, fragile: false },
        vol: { bucket: "high", readable: false, why: "degenerate" },
      },
    },
  ],
  crossTabs: [],
  dimensions: ["trend", "vol"],
  multiplicity: null,
  history: null,
  today: { session: "2026-11-23", at: "2026-11-23", buckets: { trend: "downtrend", vol: "high" } },
} as unknown as RegimeCuts;

const DATA: AutomationConditions = {
  name: "mine",
  error: null,
  cuts: CUTS,
  labels: { SPX: { trend: "downtrend", vol: "high", through: "2026-11-20" }, QQQ: { status: "unmeasured", reason: "needs 60 bars, has 39" } },
};

describe("today's conditions on an automation's page", () => {
  it("says what today's label is, from which bar, and why an underlying has none", () => {
    expect(labelWords("SPX", { trend: "range", vol: "low" })).toBe("SPX range, low volatility");
    const lead = conditionsLead("mine", DATA);
    expect(lead).toContain("through 2026-11-20");
    expect(lead).toContain("SPX downtrend, high volatility");
    expect(lead).toContain("QQQ has no label today: needs 60 bars, has 39");
    expect(conditionsLead("mine", { ...DATA, labels: {} })).toBe("No underlying of mine has daily bars to label today.");
  });

  it("renders MEIC's card with the automation's words: its record where readable, the reason where not", () => {
    const out = html(<ConditionsBody name="mine" data={DATA} />);
    expect(out).toContain("today&#x27;s conditions");
    expect(out).toContain("volatility");
    expect(out).toContain("downtrend");
    expect(out).toContain("-$50.00");
    expect(out).toContain("every session falls in one bucket");
    expect(out).not.toContain("last reading");
  });

  it("names an error instead of drawing an empty card", () => {
    expect(html(<ConditionsBody name="mine" data={{ ...DATA, error: "boom", cuts: null }} />)).toContain("boom");
    expect(html(<ConditionsBody name="mine" data={undefined} />)).toContain("Reading today");
  });
});
