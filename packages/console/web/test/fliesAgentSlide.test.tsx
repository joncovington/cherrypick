import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { AgentCheck, FliesAgentPayload } from "@console/shared";
import { AgentSlide, CHECK_SORTS, RecordBody } from "../src/pages/Flies/AgentSlide";

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
    arms: { control: "control", rule: "trend-rule", agent: "intraday-agent", live: "control" },
    liveModeMax: "gates",
    trendBandPoints: 20,
    sessions: { decided: 25, paired: [], shadow: [] },
    gates: { n: 25, mean: 150, sd: 20, lower95: 143, sessionsNeeded: 1, targetEdge: 100 },
    closes: { n: 25, mean: 0, sd: 0, lower95: null, sessionsNeeded: null, episodes: 0, saved2x: 0 },
    strandRate: { rule: 0.5, agent: 0.25 },
    shadow: { sessions: [], counted: 0, agree: 0, agreement: null, closes: { tagged: 0, saved: null, saved2x: null } },
    liveClosesBuilt: false,
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

const check = (index: number, called: boolean, over: Partial<AgentCheck> = {}): AgentCheck => ({
  at: `2026-10-06T1${String(index)}:10:00`,
  target: "paper",
  called,
  skipped: called ? null : "far from the band",
  trigger: called ? "near_band" : null,
  model: called ? "model-x" : null,
  costUsd: called ? 0.05 : null,
  seconds: called ? 4 : null,
  ok: called ? true : null,
  error: null,
  trendGate: called ? "on" : null,
  closeLabels: [],
  droppedCloses: [],
  confidence: called ? 0.6 : null,
  reason: called ? "range holding" : null,
  spot: 5000,
  index,
  ...over,
});

function render(mode: "paper" | "live", data: FliesAgentPayload = payload): string {
  const client = new QueryClient();
  client.setQueryData(["flies-agent", null], data);
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentSlide mode={mode} date={null} />
      </MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");
}

describe("the intraday agent slide", () => {
  it("leads with its tiles and the writer's offered modes", () => {
    const html = render("paper");
    expect(html).toContain("off · shadow");
    expect(html).not.toContain("gates_and_closures");
  });

  it("keeps the criteria, shadow, closes and spend out of the page: they are tabs of one lightbox", () => {
    const html = render("paper");
    expect(html).toContain("the agent&#x27;s record");
    expect(html).toContain("recorded, never executed");
    expect(html).not.toContain("sessions recorded");
    expect(html).not.toContain("live-shadow sessions");
  });

  it("shows an unscored criterion as not scored in the offer tab", () => {
    const html = renderToString(<RecordBody data={payload} tab="offer" />);
    expect(html).toContain("not scored");
    expect(html).toContain("pass");
  });

  it("lists only the model calls by default and every check once skipped checks are switched on", () => {
    const withChecks: FliesAgentPayload = { ...payload, session: "2026-10-06", checks: [check(1, true), check(2, false)] };
    const html = render("paper", withChecks);
    expect(html).toContain("model calls only");
    expect(html).toContain("1 of 2 checks");
    expect(html).not.toContain("far from the band");
    // A row's pack is a button into the lightbox, never an inline block.
    expect(html).toContain("Open the pack for 11:10");
    expect(html).not.toContain("spec-block");
  });

  it("refuses a live read rather than showing the paper arm under a live badge", () => {
    const html = render("live");
    expect(html).toContain("no live view");
    expect(html).not.toContain("what live may offer");
  });

  it("sorts checks on timing, size and fit, never on a credit", () => {
    expect(CHECK_SORTS.map((s) => s.id)).toEqual(["time", "cost", "confidence"]);
  });
});
