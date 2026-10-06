import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FliesAgentPayload } from "@console/shared";
import { AgentSlide } from "../src/pages/Flies/AgentSlide";

/**
 * The intraday agent slide renders the module's verdicts as given: a criterion the writer could not
 * score reads "not scored" (never pass, never fail), the offered modes are the file's list, and a
 * live read is refused rather than showing the paper arm under a live badge.
 */

const payload: FliesAgentPayload = {
  session: null,
  sessions: [],
  config: null,
  decision: null,
  checks: [],
  sessionSpend: { checks: 0, calls: 0, costUsd: 0 },
  dayOpen: null,
  openTags: [],
  refusals: [],
  qualificationStatus: "ok",
  now: "2026-10-06T14:00:00.000Z",
  qualification: {
    generatedAt: "2026-10-06T20:00:00+00:00",
    arms: { control: "control", rule: "trend-rule", agent: "intraday-agent" },
    liveModeMax: "gates",
    trendBandPoints: 20,
    sessions: { decided: 25, paired: [], shadow: [] },
    gates: { n: 25, mean: 150, sd: 20, lower95: 143, sessionsNeeded: 1, targetEdge: 100 },
    closes: { n: 25, mean: 0, sd: 0, lower95: null, sessionsNeeded: null, episodes: 0, saved2x: 0 },
    strandRate: { rule: 0.5, agent: 0.25 },
    criteria: [
      { id: "decision_sessions", mode: "gates", label: "sessions recorded", value: 25, threshold: 20, pass: true },
      { id: "live_shadow", mode: "gates", label: "live-shadow sessions", value: 6, threshold: 5, pass: null },
    ],
    unlocked: { off: true, shadow: true, gates: false, gates_and_closures: false },
    offeredModes: ["off", "shadow"],
    perSession: [],
    taggedCloses: [],
    spend: [],
  },
};

function render(mode: "paper" | "live"): string {
  const client = new QueryClient();
  client.setQueryData(["flies-agent", null], payload);
  return renderToString(
    <QueryClientProvider client={client}>
      <AgentSlide mode={mode} date={null} />
    </QueryClientProvider>,
  );
}

describe("the intraday agent slide", () => {
  it("shows an unscored criterion as not scored and the writer's offered modes", () => {
    const html = render("paper");
    expect(html).toContain("not scored");
    expect(html).toContain("off · shadow");
    expect(html).not.toContain("gates_and_closures");
  });

  it("refuses a live read rather than showing the paper arm under a live badge", () => {
    const html = render("live");
    expect(html).toContain("paper-only");
    expect(html).not.toContain("what live may offer");
  });
});
