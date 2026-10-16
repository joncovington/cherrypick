import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { AdvisorExperiment, AdvisorPair, AdvisorPaired } from "@console/shared";

import { ExperimentRollup, PairTable } from "../src/pages/Advisor/AdvisorPage";
import { PairedRead } from "../src/components/advisor/PairedRead";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

function paired(over: Partial<AdvisorPaired> = {}): AdvisorPaired {
  return {
    sessions: 8,
    paired_on: "session",
    mean_diff: 28.25,
    total_diff: 226,
    ci: [7.5, 37.5],
    ci_level: 0.95,
    ci_excludes_zero: true,
    t_lower: 12.4,
    advised_better: 6,
    base_better: 2,
    ties: 0,
    sign_test_p: 0.2891,
    sessions_to_decision: 0,
    advised_only_sessions: 0,
    base_only_sessions: 1,
    ...over,
  };
}

function pair(p: AdvisorPaired | null | undefined): AdvisorPair {
  return {
    advisedTag: "advised:try-it",
    baseTag: "control",
    advised: { net_pnl: 120.5, win_rate: 0.7, sample: 9, sessions: 8 },
    base: { net_pnl: 100, win_rate: 0.75, sample: 9, sessions: 8 },
    delta: { net_pnl: 20.5 },
    qualification: {},
    underpowered: true,
    ...(p === undefined ? {} : { paired: p }),
  };
}

describe("the verdict's paired read", () => {
  it("states the interval, the sign test and the sessions to decision from the verdict", () => {
    const html = text(<PairTable pairs={[pair(paired())]} />);
    expect(html).toContain("paired over 8 sessions both books traded");
    expect(html).toContain("+$28.25 a session");
    expect(html).toContain("95% interval [+$7.50, +$37.50]");
    expect(html).toContain("advised ahead on 6, base on 2");
    expect(html).toContain("sign test p = 0.2891");
    expect(html).toContain("the interval already excludes zero");
    expect(html).toContain("1 session only one book closed, not paired");
  });

  it("projects the sessions still needed, and says what a multi-day pair keyed on", () => {
    const html = text(<PairedRead paired={paired({ paired_on: "entry_session", sessions_to_decision: 12, ci: [-5, 40] })} />);
    expect(html).toContain("both books entered");
    expect(html).toContain("about 12 more sessions for the interval to exclude zero at this mean and spread");
  });

  it("tones nothing: no sign or verdict class on the paired read, whatever the interval says", () => {
    for (const p of [paired(), paired({ mean_diff: -40, ci: [-60, -20] })]) {
      const html = text(<PairedRead paired={p} />);
      expect(html).not.toMatch(/pnl-pos|pnl-neg|chip/);
    }
  });

  it("says a stored verdict predates the read, and that no shared session means no read", () => {
    expect(text(<PairTable pairs={[pair(undefined)]} />)).toContain("not on this verdict yet");
    expect(text(<PairedRead paired={null} />)).toContain("no session both books closed");
    expect(text(<PairedRead paired={paired({ sessions: 1, ci: null, sign_test_p: 1, sessions_to_decision: null })} />)).toContain(
      "(one session: no interval)",
    );
  });
});

describe("the at-a-glance roll-up", () => {
  const exp = (id: string, module: string, net: number | null): AdvisorExperiment =>
    ({
      id,
      module,
      name: id,
      status: "active",
      sessionsRun: 3,
      expiresAfter: 10,
      journal: [],
      verdict: net === null ? null : { pairs: [{ ...pair(paired()), delta: { net_pnl: net } }] },
    }) as unknown as AdvisorExperiment;

  it("sorts on module, name, status, progress and net delta, and starts in the advisor's order", () => {
    const html = text(<ExperimentRollup experiments={[exp("zeta", "flies", 10), exp("alpha", "bwb", null)]} />);
    for (const label of ["Module", "Experiment", "Status", "Progress", "Net delta"]) {
      expect(html).toContain(`<button type="button" class="th-sort">${label}</button>`);
    }
    // Last session and the gate are composite cells: plain headers.
    expect(html).toContain("<th>Last session</th>");
    expect(html).toContain("<th>Gate</th>");
    expect(html.indexOf(">zeta<")).toBeLessThan(html.indexOf(">alpha<"));
  });
});
