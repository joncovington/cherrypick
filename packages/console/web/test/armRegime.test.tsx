import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { RegimeArm, RegimeCell, RegimeCuts, RegimeCutsPayload } from "@console/shared";
import { RegimeSummary } from "../src/components/analysis/ArmRegime";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

function cell(bucket: string, over: Partial<RegimeCell> = {}): RegimeCell {
  return {
    bucket, valueMin: null, valueMax: null, sessions: 8, trades: 60, netPnl: 900, avgPnl: 15, winRate: 0.8,
    profitFactor: 1.5, completed: 40, completionRate: 0.67, thin: false, fragile: false, robustness: null,
    history: null, ...over,
  };  // fmt: skip
}

function arm(name: string): RegimeArm {
  return {
    arm: name, eraStart: "2026-09-29", eraBreak: null, sessions: 14, trades: 120, netPnl: 400, winRate: 0.78,
    completed: null, completionRate: null, thin: false,
    dimensions: {
      gex: {
        coveragePct: 1, tagged: 120, untagged: 0, sessions: 14, effectiveN: 14, dailyScale: true,
        degenerate: false, underpowered: false, paired: [],
        buckets: [cell("positive"), cell("negative", { netPnl: -500, thin: true, fragile: null, sessions: 2 })],
      },
      trend: {
        coveragePct: 1, tagged: 120, untagged: 0, sessions: 14, effectiveN: 14, dailyScale: true,
        degenerate: false, underpowered: false, paired: [],
        buckets: [cell("up", { netPnl: -700, fragile: true })],
      },
    },
  };  // fmt: skip
}

function ok(arms: RegimeArm[], stale: { artifactSession: string; latestSession: string } | null = null): RegimeCutsPayload {
  const cuts = {
    cutVersion: 2, module: "flies", generatedAt: null, session: "2026-10-16", symbol: "SPX", armColumn: "arm",
    entryModes: null, thinBelowSessions: 3, minEffectiveN: null,
    era: { start: null, key: null, boundingBreak: null, breaks: [], ignoredFuture: [], caveats: [] },
    arms, crossTabs: [], dimensions: ["gex", "trend"], multiplicity: null, history: null,
  } as RegimeCuts;  // fmt: skip
  return { status: "ok", cuts, stale, sessions: ["2026-10-16"] };
}

describe("RegimeSummary", () => {
  it("names its own window, which is not the rest of the card's", () => {
    const html = text(<RegimeSummary payload={ok([arm("control")])} tag="control" />);
    expect(html).toContain("a different window from the rest of this card");
    expect(html).toContain("nightly cut of 2026-10-16");
    expect(html).toContain("era from 2026-09-29");
  });

  it("passes the writer's thin and fragile stamps through, and tones neither", () => {
    const html = text(<RegimeSummary payload={ok([arm("control")])} tag="control" />);
    expect(html).toMatch(/class="pnl-pos">\$900\.00/);
    expect(html).toContain(">thin<");
    expect(html).toContain(">fragile<");
    expect(html).toMatch(/<td>-\$500\.00<\/td>/);
    expect(html).toMatch(/<td>-\$700\.00<\/td>/);
  });

  it("finds an advised book's arm under its experiment tag", () => {
    const html = text(<RegimeSummary payload={ok([arm("advised:x")])} tag="advised:x@exp-2026-10-01-flies-1" />);
    expect(html).toContain("positive");
  });

  it("says when the arm is not in the cut, the cut is absent, or it failed", () => {
    expect(text(<RegimeSummary payload={ok([arm("control")])} tag="range-high" />)).toContain("not in the 2026-10-16");
    expect(text(<RegimeSummary payload={{ status: "absent", path: "x", sessions: [] }} tag="control" />)).toContain(
      "no nightly regime cut",
    );
    expect(text(<RegimeSummary payload={{ status: "failed", error: "cut_version 9", sessions: [] }} tag="control" />)).toContain(
      "cut_version 9",
    );
  });

  it("says when the cut is older than the ledger", () => {
    const html = text(
      <RegimeSummary payload={ok([arm("control")], { artifactSession: "2026-10-15", latestSession: "2026-10-16" })} tag="control" />,
    );
    expect(html).toContain("older than the ledger, which has 2026-10-16");
  });
});
