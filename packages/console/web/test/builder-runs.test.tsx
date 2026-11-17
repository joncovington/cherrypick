import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderRuns } from "@console/shared";
import { BuilderCard, BuilderRunsView } from "../src/pages/Builder/BuilderPage";

const html = (el: React.ReactElement) =>
  renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const A: BuilderAutomation = {
  name: "mine",
  group: null,
  symbol: "SPX",
  enabled: false,
  note: null,
  start: null,
  register: null,
  summary: "nothing backtested",
  blocks: [{ block: "structure", state: "not_backtested", keys: {} }],
  forward: null,
  suggestion: null,
};

const POSSIBLE = { blocks: ["structure"], window: ["2025-11-14", "2026-11-13"] as [string, string], needs: [], possible: true };

describe("the Builder's backtest button", () => {
  it("is offered only where our data covers the automation", () => {
    expect(html(<BuilderCard a={A} />)).not.toContain(">Backtest<");
    expect(html(<BuilderCard a={{ ...A, suggestion: { ...POSSIBLE, possible: false } }} />)).not.toContain(">Backtest<");
    expect(html(<BuilderCard a={{ ...A, suggestion: POSSIBLE }} />)).toContain(">Backtest<");
  });
});

describe("the run card", () => {
  const data: BuilderRuns = {
    name: "mine",
    tasks: [
      { id: 402, run: "builder-mine-2611161800", status: "failed", queued_at: null, started_at: null, finished_at: null, exit_code: 2, note: "no source covers" },
      { id: 401, run: "builder-mine-2611161730", status: "done", queued_at: null, started_at: null, finished_at: null, exit_code: 0, note: null },
    ],
    runs: [
      {
        run: "builder-mine-2611161730",
        symbol: "SPX",
        source: "recorded-0dte",
        licensed: false,
        cadence: "stored minutes",
        sessions: ["2026-10-13", "2026-11-13"],
        session_count: 23,
        summary: { mine: { sessions: 23, trades: 20, net: 150, mean_net: 7.5 } },
        still_open: 0,
        generated_at: "2026-11-16T17:31:00-05:00",
      },
    ],
    withheld: 1,
    results_unavailable: false,
  };

  it("lists each queued run and each result's figures as the run wrote them, as description", () => {
    const out = html(<BuilderRunsView data={data} />);
    expect(out).toContain("no trial and no verdict");
    expect(out).toContain("builder-mine-2611161800: failed (no source covers)");
    expect(out).toContain("builder-mine-2611161730: done");
    expect(out).toContain("SPX on recorded-0dte, 2026-10-13 to 2026-11-13 (23 sessions, stored minutes)");
    expect(out).toContain("<td>mine</td><td>23</td><td>20</td>");
    expect(out).toContain("1 licensed result(s) not shown.");
  });

  it("says so when nothing was queued", () => {
    const out = html(<BuilderRunsView data={{ ...data, tasks: [], runs: [], withheld: 0 }} />);
    expect(out).toContain("No backtest queued from here yet.");
  });
});
