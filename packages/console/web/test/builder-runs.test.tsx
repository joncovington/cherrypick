import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderRunCard, BuilderRuns } from "@console/shared";
import { BuilderCard } from "../src/pages/Builder/BuilderPage";
import { changeLine, chartPaths, clock, ExploreRunsList, legChain, RunCardView, runLabel } from "../src/pages/Builder/BuilderRunCard";

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

const RUN = {
  symbol: "SPX",
  source: "recorded-0dte",
  licensed: false,
  cadence: "stored minutes",
  sessions: ["2026-10-13", "2026-11-13"] as [string, string],
  session_count: 23,
  summary: { mine: { sessions: 23, trades: 20, net: 150, mean_net: 7.5 } },
  still_open: 0,
  generated_at: "2026-11-16T17:31:00-05:00",
  spark: [0, 40, 20, 150],
  net: 150,
};

describe("the explore-runs list", () => {
  const data: BuilderRuns = {
    name: "mine",
    tasks: [
      { id: 403, run: "builder-mine-2611161900", status: "queued", queued_at: null, started_at: null, finished_at: null, exit_code: null, note: null },
      { id: 402, run: "builder-mine-2611161800", status: "failed", queued_at: null, started_at: null, finished_at: null, exit_code: 2, note: "no source covers" },
    ],
    runs: [
      { ...RUN, run: "builder-mine-2611161730", changes: [{ key: "exit.stop", before: 1.5, after: 2 }] },
      { ...RUN, run: "builder-mine-2611151730", changes: [] },
    ],
    withheld: 1,
    results_unavailable: false,
  };

  it("names each run by its time, says what it changed, and shows the queue and failures", () => {
    const out = html(<ExploreRunsList data={data} onOpen={() => {}} />);
    expect(out).toContain(">11-16 17:30</button>");
    expect(out).toContain("exit.stop 1.5 → 2");
    expect(out).toContain("first run");
    expect(out).toContain("task 403 · builder-mine-2611161900 · queued");
    expect(out).toContain("11-16 18:00 failed: no source covers");
    expect(out).toContain("Explore runs are not trials");
    expect(out).toContain("1 licensed result(s) not shown.");
  });

  it("says so when nothing was queued", () => {
    expect(html(<ExploreRunsList data={{ ...data, tasks: [], runs: [], withheld: 0 }} onOpen={() => {}} />)).toContain(
      "No backtest queued from here yet.",
    );
  });

  it("labels a run and its changes the page's way", () => {
    expect(runLabel("builder-mine-2611161730")).toBe("11-16 17:30");
    expect(clock(630)).toBe("10:30");
    expect(clock(965)).toBe("16:05");
    expect(clock("12:00")).toBe("12:00");
    const legs = [{ side: -1, right: "P", strike: 5900 }, { side: 1, right: "P", strike: 5880 }];
    const t = { legs, quantity: 2, expiry: "2026-11-16" } as unknown as Parameters<typeof legChain>[0];
    expect(legChain(t).split("\n")).toEqual(["STO 2 2026-11-16 5900P", "BTO 2 2026-11-16 5880P"]);
    expect(changeLine([], false)).toBe("first run");
    expect(changeLine([], true)).toBe("no change");
    const three = ["a", "b", "c"].map((k) => ({ key: k, before: 1, after: 2 }));
    expect(changeLine(three, true)).toBe("a 1 → 2; b 1 → 2 (+1 more)");
  });
});

describe("one run's card", () => {
  const card: BuilderRunCard = {
    run: "builder-mine-2611161730",
    automation: "mine",
    symbol: "SPX",
    source: "recorded-0dte",
    store: "gex/history.db",
    cadence: "stored minutes",
    sessions: ["2026-10-13", "2026-11-13"],
    session_count: 23,
    still_open: 0,
    generated_at: null,
    licensed: false,
    tiles: { net: 10, gross: 40, trades: 4, sessions: 23, per_trade: 2.5, win_rate: 0.5, max_dd: -130, net_over_dd: 0.08 },
    curve: [
      { i: 1, session: "2025-03-03", net: 100, gross: 110, dd: 0 },
      { i: 2, session: "2025-06-02", net: 20, gross: 40, dd: -80 },
      { i: 3, session: "2026-01-02", net: -30, gross: 0, dd: -130 },
    ],
    years: [
      { period: "2025", trades: 2, gross: 40, fees: 2.6, settlement: 12, slippage: 1, net: 24.4, max_dd: -80 },
      { period: "All", trades: 2, gross: 40, fees: 2.6, settlement: 12, slippage: 1, net: 24.4, max_dd: -80 },
    ],
    skips: [{ reason: "no_signal", sessions: 19 }],
    previous: "builder-mine-2611151730",
    changes: [{ key: "exit.stop", before: 1.5, after: 2 }],
    trades: [],
  };

  it("draws the tiles, the years with every money column, the skips and the change", () => {
    const out = html(<RunCardView card={card} />);
    expect(out).toContain("explore · descriptive, not a verdict");
    expect(out).toContain("of 23 sessions");
    expect(out).toContain("50%");
    expect(out).toContain('<th scope="col">Settlement</th>');
    expect(out).toContain("no signal");
    expect(out).toContain("exit.stop: 1.5 → 2");
    expect(out).toContain("11-15 17:30 → 11-16 17:30");
    expect(out).toMatch(/<button[^>]*disabled[^>]*>Declare this spec…<\/button>/);
  });

  it("puts the worst drawdown lowest and the zero line between the net's high and low", () => {
    const p = chartPaths(card.curve);
    const ys = [...p.dd.matchAll(/L[\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(Math.max(...ys)).toBeCloseTo(p.ddTop + 60);
    expect(Number(p.zero)).toBeGreaterThan(8);
    expect(Number(p.zero)).toBeLessThan(212);
  });
});
