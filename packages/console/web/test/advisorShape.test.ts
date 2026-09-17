import { describe, it, expect } from "vitest";
import { normalizeAdvisorPayload, normalizeApplyStatus, normalizeModulePayload } from "../src/lib/advisorShape";

/**
 * The page is served off `web/dist` the moment it is built; the server is whatever the supervisor
 * last started. So every build opens a window where a new page reads an old API, and on
 * 2026-09-17 that window blanked the advisor page on the live console: `active` was an object
 * where the page read a list, `enactment` a value where it read `enactments`. These pin that both
 * shapes come through the boundary as the one the page reads.
 */

const experiment = {
  id: "exp-1",
  module: "meic",
  baseProfile: "control",
  name: "wider stop",
  hypothesis: null,
  successMetric: null,
  params: {},
  status: "active",
  createdSession: "2026-09-01",
  sessionsRun: 3,
  expiresAfter: 15,
  verdict: null,
  journal: [],
};

describe("the module payload", () => {
  it("lifts the pre-2026-09-17 single active experiment into a list, carrying its progress", () => {
    const out = normalizeModulePayload({
      module: "meic",
      storePresent: true,
      active: experiment,
      queued: [],
      concluded: [],
      sessions: [],
      calendarSessions: 11,
      stallBudget: 30,
      tomorrow: null,
    });
    expect(out.active).toHaveLength(1);
    expect(out.active[0]).toMatchObject({ id: "exp-1", tag: null, calendarSessions: 11, stallBudget: 30 });
  });

  it("reads null active as no experiments", () => {
    const out = normalizeModulePayload({ module: "meic", storePresent: true, active: null, queued: [], concluded: [], sessions: [], calendarSessions: null, stallBudget: null, tomorrow: null });
    expect(out.active).toEqual([]);
  });

  it("passes the current list shape through unchanged", () => {
    const out = normalizeModulePayload({
      module: "meic",
      storePresent: true,
      active: [{ ...experiment, tag: "advised:wider-stop", calendarSessions: 2, stallBudget: 30 }],
      queued: [],
      concluded: [],
      sessions: [],
      tomorrow: null,
    });
    expect(out.active[0]).toMatchObject({ tag: "advised:wider-stop", calendarSessions: 2, stallBudget: 30 });
  });
});

describe("the apply status", () => {
  const legacy = {
    module: "meic",
    nextSession: "2026-09-18",
    artifactWritten: true,
    artifactProposals: [{ param: "entry_window_end", value: "13:30", rationale: "exp" }],
    artifactRejected: [],
    consumerDecision: { day: "2026-09-17", params: { entry_window_end: "13:30" }, reason: null, experiment_id: "exp-1" },
    disabledReason: null,
    enactment: { session: "2026-09-17", status: "enacted", detail: null, experimentId: "exp-1", decisionReason: null, scoredAt: null },
  };

  it("lifts a single enactment into the list and synthesises the experiment entries", () => {
    const out = normalizeApplyStatus(legacy);
    expect(out.enactments).toHaveLength(1);
    expect(out.enactments[0]?.status).toBe("enacted");
    expect(out.artifactExperiments).toHaveLength(1);
    expect(out.artifactExperiments[0]?.proposals[0]?.param).toBe("entry_window_end");
    expect(out.decisionExperiments[0]).toMatchObject({ experimentId: "exp-1", params: { entry_window_end: "13:30" } });
  });

  it("reads a null legacy enactment as not scored, and no artifact as no entries", () => {
    const out = normalizeApplyStatus({ ...legacy, enactment: null, artifactWritten: false, consumerDecision: null });
    expect(out.enactments).toEqual([]);
    expect(out.artifactExperiments).toEqual([]);
    expect(out.decisionExperiments).toEqual([]);
  });

  it("normalises every module on the page payload", () => {
    const out = normalizeAdvisorPayload({ sessions: [], session: null, latest: [], checkpoints: [], proposals: [], experiments: [experiment], applyStatus: [legacy], storePresent: true });
    expect(out.applyStatus[0]?.enactments).toHaveLength(1);
    expect(out.experiments[0]?.tag).toBeNull();
  });
});
