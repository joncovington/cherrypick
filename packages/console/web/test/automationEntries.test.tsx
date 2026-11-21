import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationEntries, AutomationsEntries, EntryFunnel } from "@console/shared";
import { EntriesBody, EntriesTable, reasonWords } from "../src/pages/Automations/AutomationEntries";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const RUN = "builder-mine-2611201000";

function funnel(due: number, entered: number, stops: Record<string, number>): EntryFunnel {
  const order = [
    ["due", "Gave a known reason"],
    ["data", "Had its data"],
    ["trigger", "Its entry fired"],
    ["limits", "Within its limits and calendar"],
    ["gates", "Passed its market gates"],
    ["structure", "Found its structure"],
    ["size", "Sized within its budget"],
  ] as const;
  let left = due;
  const steps = order.map(([step, label]) => {
    const stopped = stops[step] ?? 0;
    const s = { step, label, reached: left, reached_share: due ? left / due : null, stopped, passed: left - stopped, passed_share: due ? (left - stopped) / due : null, causes: [] };
    left -= stopped;
    return s;
  });
  return { due, pending: 1, entered, missed: due - entered, rate: due ? entered / due : null, steps, most_lost: null };
}

const PAPER = { ...funnel(10, 6, { data: 1, gates: 3 }), most_lost: { step: "gates" as const, label: "Passed its market gates", stopped: 3, share: 0.3 } };
const RUNF = funnel(100, 80, { gates: 20 });

const FULL: AutomationEntries = {
  name: "mine",
  pinned: RUN,
  withheld: false,
  error: null,
  reference: { run: RUN, licensed: false, first: "2025-11-03", last: "2026-11-02", sessions: 100 },
  first: "2026-11-02",
  last: "2026-11-13",
  paper: PAPER,
  run: RUNF,
  causes: [
    { reason: "no_fresh_quote", step: "data", paper: 0.1, run: 0 },
    { reason: "gate:vix_level", step: "gates", paper: 0.3, run: 0.2 },
  ],
  days: [
    { session: "2026-11-13", outcome: "missed", reason: "gate:vix_level", step: "gates" },
    { session: "2026-11-12", outcome: "entered", reason: null, step: null },
  ],
};

describe("an automation's entries", () => {
  it("words a reason's spelling and nothing else", () => {
    expect(reasonWords("gate:vix_level")).toBe("gate: vix level");
    expect(reasonWords("no_fresh_quote")).toBe("no fresh quote");
    expect(reasonWords("strike:short_put")).toBe("strike: short put");
  });

  it("draws each step for paper and the run, the causes side by side and the latest sessions", () => {
    const out = html(<EntriesBody name="mine" data={FULL} />);
    expect(out).toContain("60% vs 80%");
    expect(out).toContain("3 sessions stopped there, 30% of those due");
    expect(out).toContain("the run");
    // Paper's bar at the gates step is the share past it (6 of 10), the run's 80 of 100.
    expect(out).toContain('class="ae-bar-fill is-paper" style="width:60%"');
    expect(out).toContain('class="ae-bar-fill is-run" style="width:80%"');
    // Due tops the funnel; what passes the last step is what entered.
    expect(out).toContain(">Due<");
    expect(out).toContain("Sized within its budget: entered");
    expect(out).toContain("gate: vix level");
    expect(out).toContain("2026-11-13");
    // The `due` step (a reason no step names) lost nothing either side, so it is not drawn.
    expect(out).not.toContain("Gave a known reason");
  });

  it("stands alone without a run, and says how to get one", () => {
    const out = html(<EntriesBody name="mine" data={{ ...FULL, pinned: null, reference: null, run: null }} />);
    expect(out).toContain("Pin a Builder run");
    expect(out).not.toContain("is-run");
    expect(out).not.toContain(" vs ");
  });

  it("says when the run is withheld", () => {
    const out = html(<EntriesBody name="mine" data={{ ...FULL, withheld: true, reference: null, run: null }} />);
    expect(out).toContain("withheld");
    expect(out).not.toContain("is-run");
  });

  it("says no session has been due yet", () => {
    const out = html(<EntriesBody name="mine" data={{ ...FULL, paper: funnel(0, 0, {}), causes: [], days: [] }} />);
    expect(out).toContain("No session has been due yet (1 window still open today)");
  });

  it("lists every automation with a link to its page", () => {
    const data: AutomationsEntries = {
      error: null,
      automations: [
        { name: "mine", group: "g", symbol: "SPX", enabled: true, configured: true, first: "2026-11-02", last: "2026-11-13", funnel: PAPER },
        { name: "gone", group: null, symbol: null, enabled: false, configured: false, first: null, last: null, funnel: funnel(0, 0, {}) },
      ],
    };
    const out = html(<EntriesTable data={data} />);
    expect(out).toContain('href="/automations/automation/mine"');
    expect(out).toContain("removed");
    expect(out).toContain("<th");
    expect(out).toContain(">gates<");
    expect(out).not.toContain(">other<");
    expect(out).not.toContain('href="/automations/automation/gone"');
  });
});
