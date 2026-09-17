import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { AdvisorActiveExperiment, AdvisorExperiment, AdvisorModulePayload } from "@console/shared";

import { AdvisorSlideBody, SessionStrip, SessionStrips } from "../src/components/advisor/AdvisorSlide";
import { gateDistance, lastCounted } from "../src/components/advisor/experimentStats";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

function experiment(over: Partial<AdvisorExperiment> = {}): AdvisorExperiment {
  return {
    id: "exp-2026-08-27-bwb-1",
    module: "bwb",
    baseProfile: "control",
    name: "flip-buffer-widen-vs-control",
    tag: "advised:flip-buffer-widen-vs-control",
    hypothesis: "a wider buffer arms less often and keeps more of the add-on credit",
    successMetric: "net delta",
    params: { flip_buffer: 1.02 },
    status: "active",
    createdSession: "2026-08-27",
    sessionsRun: 9,
    expiresAfter: 15,
    verdict: {
      pairs: [
        {
          module: "bwb",
          advisedTag: "advised:control",
          baseTag: "control",
          advised: { net_pnl: 120.5, win_rate: 0.7, sample: 9, days: 2 },
          base: { net_pnl: 336.69, win_rate: 0.75, sample: 9, days: 2 },
          delta: { net_pnl: -216.19, win_rate: -0.05, return_on_capital: null, sharpe: null },
          qualification: {},
          underpowered: true,
          rule: { min_sample: 20, min_days: 14, min_win_rate: 0.6 },
        } as never,
      ],
      underpowered: true,
      recommendation: null,
      stalled: null,
    },
    journal: [
      { session: "2026-09-10", event: "counted", detail: { status: "enacted" }, createdAt: null },
      { session: "2026-09-11", event: "counted", detail: { status: "not_enacted" }, createdAt: null },
    ],
    ...over,
  };
}

function active(over: Partial<AdvisorActiveExperiment> = {}): AdvisorActiveExperiment {
  return { ...experiment(), calendarSessions: 11, stallBudget: 30, ...over };
}

function payload(over: Partial<AdvisorModulePayload> = {}): AdvisorModulePayload {
  return {
    module: "bwb",
    storePresent: true,
    active: [active()],
    queued: [experiment({ id: "exp-2026-09-11-bwb-1", name: "flip-buffer-near-control", status: "queued", sessionsRun: 0 })],
    concluded: [],
    sessions: [
      { session: "2026-09-09", status: "enacted", experimentId: "exp-2026-08-27-bwb-1", detail: null },
      { session: "2026-09-10", status: "carried", experimentId: "exp-2026-08-27-bwb-1", detail: "frozen" },
      { session: "2026-09-11", status: "not_enacted", experimentId: "exp-2026-08-27-bwb-1", detail: null },
    ],
    tomorrow: {
      module: "bwb",
      nextSession: "2026-09-14",
      artifactWritten: true,
      artifactProposals: [{ param: "flip_buffer", value: 1.02, rationale: "exp" }],
      artifactRejected: [],
      artifactExperiments: [
        {
          experimentId: "exp-2026-08-27-bwb-1",
          name: null,
          tag: null,
          base: null,
          proposals: [{ param: "flip_buffer", value: 1.02, rationale: "exp" }],
          rejected: [],
        },
      ],
      consumerDecision: null,
      decisionExperiments: [],
      disabledReason: null,
      enactments: [],
    },
    ...over,
  };
}

describe("the module's advisor slide", () => {
  it("says how far the experiment has got and where it stalls", () => {
    const html = text(<AdvisorSlideBody data={payload()} />);
    expect(html).toContain("9 of 15 sessions enacted");
    expect(html).toContain("11 calendar sessions since it started, stalls at 30");
  });

  it("draws one cell per scored session with its status", () => {
    const html = text(<SessionStrip cells={payload().sessions} />);
    expect(html).toContain("advisor-cell-enacted");
    expect(html).toContain("advisor-cell-carried");
    expect(html).toContain("advisor-cell-not_enacted");
    expect(html).toContain("2026-09-10: carried — frozen");
  });

  it("shows tomorrow's artifact and the queue", () => {
    const html = text(<AdvisorSlideBody data={payload()} />);
    expect(html).toContain("written");
    expect(html).toContain("flip_buffer = 1.02");
    expect(html).toContain("flip-buffer-near-control");
  });

  it("renders an honest empty view when nothing runs on the module", () => {
    const html = text(<AdvisorSlideBody data={payload({ active: [], queued: [], sessions: [] })} />);
    expect(html).toContain("no active experiment");
    expect(html).toContain("no scored sessions yet");
  });

  it("renders every active experiment on its own book, with its own progress and strip", () => {
    // Two experiments on one base at once (2026-09-17): nothing on the slide may assume one.
    const second = active({
      id: "exp-2026-09-11-bwb-1",
      name: "flip-buffer-near-control",
      tag: "advised:flip-buffer-near-control",
      sessionsRun: 2,
      calendarSessions: 3,
    });
    const data = payload({
      active: [active(), second],
      queued: [],
      sessions: [
        ...payload().sessions,
        { session: "2026-09-10", status: "enacted", experimentId: "exp-2026-09-11-bwb-1", detail: null },
        { session: "2026-09-11", status: "enacted", experimentId: "exp-2026-09-11-bwb-1", detail: null },
      ],
      tomorrow: {
        ...payload().tomorrow!,
        artifactExperiments: [
          { experimentId: "exp-2026-08-27-bwb-1", name: "flip-buffer-widen-vs-control", tag: "advised:flip-buffer-widen-vs-control", base: "control", proposals: [{ param: "flip_buffer", value: 1.02, rationale: "exp" }], rejected: [] },
          { experimentId: "exp-2026-09-11-bwb-1", name: "flip-buffer-near-control", tag: "advised:flip-buffer-near-control", base: "control", proposals: [{ param: "flip_buffer", value: 1.001, rationale: "exp" }], rejected: [] },
        ],
      },
    });
    const html = text(<AdvisorSlideBody data={data} />);
    expect(html).toContain("advised:flip-buffer-widen-vs-control");
    expect(html).toContain("advised:flip-buffer-near-control");
    expect(html).toContain("9 of 15 sessions enacted");
    expect(html).toContain("2 of 15 sessions enacted");
    expect(html).toContain("3 calendar sessions since it started, stalls at 30");
    // Tomorrow's artifact lists both experiments' admitted params.
    expect(html).toContain("flip_buffer = 1.02");
    expect(html).toContain("flip_buffer = 1.001");
    expect(html).toContain("2 experiments");
    // One strip per experiment, labelled.
    const strips = text(<SessionStrips cells={data.sessions} active={data.active} />);
    expect(strips.split("advisor-strip-label").length - 1).toBe(2);
    expect(strips).toContain("flip-buffer-near-control");
  });

  it("keeps a single experiment's strip unlabelled and lists the module's own unattributed cells once", () => {
    const one = text(<SessionStrips cells={payload().sessions} active={payload().active} />);
    expect(one).not.toContain("advisor-strip-label");
    const withBare = text(
      <SessionStrips
        cells={[...payload().sessions, { session: "2026-09-12", status: "no_artifact", experimentId: null, detail: null }]}
        active={payload().active}
      />,
    );
    expect(withBare).toContain("no experiment");
  });

  it("reports the gate distance and the last scored session for the roll-up", () => {
    expect(gateDistance(experiment())).toBe("trades 9 of 20 · days 2 of 14");
    expect(lastCounted(experiment())).toEqual({ session: "2026-09-11", status: "not_enacted" });
    expect(lastCounted(experiment({ journal: [] }))).toBeNull();
  });
});
