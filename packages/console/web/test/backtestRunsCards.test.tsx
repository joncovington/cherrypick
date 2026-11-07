import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BacktestArmCards, BacktestRunListing, BacktestRunReport } from "@console/shared";
import { ArmIntervals, RunDialogBody, RunTile, orderedRuns, runModules, type RunTab } from "../src/pages/Backtest/RunsCards";

/**
 * The Backtest runs tab as cards (2026-11-03): one tile per replay, newest first, opening to a
 * lightbox. Every figure is the report's and every verdict the study's; an interval is never toned.
 */

const wrap = (node: React.ReactNode) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

function listing(over: Partial<BacktestRunListing>): BacktestRunListing {
  return {
    run_id: "20261028-065012-0174bd",
    created_at: 1_793_000_000,
    module: "meic",
    window: "design",
    provisional: true,
    arms: ["control", "d12", "d20"],
    span: ["2022-05-16", "2025-06-30"],
    cadence: 60,
    sessions: 778,
    refused: 3,
    ...over,
  };
}

const cards = (flags: number): BacktestArmCards =>
  ({
    equity: [
      { session: "2024-01-02", net: 5, cum: 5, drawdown: 0 },
      { session: "2024-01-03", net: -2, cum: 3, drawdown: 2 },
    ],
    calendar: { months: { "2024-01": 3 }, years: [{ year: "2024", net: 3, sessions: 2, traded: 2, winning: 1, max_drawdown: 2 }] },
    health: { flags, checks: [{ check: "sample", flag: flags > 0, value: 2, bar: 100, sentence: "two sessions against a bar of 100" }] },
  }) as unknown as BacktestArmCards;

const REPORT = {
  run_id: "20261028-065012-0174bd",
  window: "design",
  span: ["2022-05-16", "2025-06-30"],
  cadence: 60,
  fill_model: "mid",
  code_commit: "abc1234",
  data_fingerprint: "fp-1",
  trials_for_module: 0,
  refused: {},
  arms: {
    control: { spec_hash: "c", summary: { sessions: 778, mean: 4.5, total: 3500 }, trades: 778 },
    d12: { spec_hash: "a", summary: { sessions: 778, mean: -2.25 }, trades: 700, vs_control: { sessions: 778, mean_diff: -6.75, ci: [-9, -4] } },
    d20: { spec_hash: "b", summary: { sessions: 778, mean: 6 }, trades: 760, vs_control: { sessions: 778, mean_diff: 1.5, ci: [-1, 4] } },
  },
  cards: { ok: true, arms: { control: cards(0), d12: cards(2), d20: cards(0) } },
} as unknown as BacktestRunReport;

const body = (tab: RunTab, report: BacktestRunReport | null = REPORT, arm: string | null = null) =>
  wrap(
    <RunDialogBody
      listing={listing({})}
      report={report}
      tab={tab}
      onTab={() => undefined}
      arm={arm}
      onArm={() => undefined}
      position="1 of 1"
      onPrev={() => undefined}
      onNext={() => undefined}
      onClose={() => undefined}
      status={<p>loading the report</p>}
    />,
  );

describe("runs as cards", () => {
  it("orders runs newest first, filters by module, and lists each module once", () => {
    const runs = [listing({ run_id: "a", created_at: 1, module: "bwb" }), listing({ run_id: "b", created_at: 3 }), listing({ run_id: "c", created_at: 2 })];
    expect(orderedRuns(runs, null).map((r) => r.run_id)).toEqual(["b", "c", "a"]);
    expect(orderedRuns(runs, "bwb").map((r) => r.run_id)).toEqual(["a"]);
    expect(runModules(runs)).toEqual(["meic", "bwb"]);
  });

  it("a tile names the study and its verdict, or says no study names the run", () => {
    const hit = { row: { study: "item23", title: "The short delta of MEIC's live rule: 12, 20 against 16" }, result: { summary: "fail" } };
    const out = wrap(<RunTile r={listing({})} hit={hit as never} hero nowMs={0} onOpen={() => undefined} />);
    expect(out).toContain("The short delta of MEIC&#x27;s live rule");
    expect(out).not.toContain("12, 20 against 16");
    expect(out).toContain('class="chip chip-missing verdict-fail">fail<');
    expect(out).toContain(">d12<");
    expect(out).toContain("provisional");
    expect(wrap(<RunTile r={listing({ provisional: false })} hero={false} nowMs={0} onOpen={() => undefined} />)).toContain("No study in the register names this run.");
  });

  it("draws each arm's interval against control untoned, its own net toned by sign", () => {
    const out = wrap(<ArmIntervals report={REPORT} result={{ verdicts: [{ arm: "d12", verdict: "fail" }] } as never} />);
    expect(out.indexOf(">control<")).toBeLessThan(out.indexOf(">d12<"));
    expect(out).toContain('class="study-arm-mean pnl-neg">-$2.25 / session');
    expect(out).toContain('class="study-arm-mean pnl-pos">+$6.00 / session');
    expect(out.match(/class="r-bar-ci"/g)).toHaveLength(2);
    expect(out).not.toMatch(/r-bar-ci[^"]*pnl/);
    expect(out).toContain("d12 against control: -$9.00 to -$4.00");
    expect(out).toMatch(/class="chip chip-missing verdict-fail">fail</);
    // Control on the same scale: no note.
    expect(out).not.toContain("sampling arm");
    // Control trading on another scale (MEIC's sampling arm) is said so beside the intervals.
    const wide = { ...REPORT, arms: { ...REPORT.arms, control: { ...REPORT.arms["control"]!, trades: 778 * 280 } } } as BacktestRunReport;
    expect(wrap(<ArmIntervals report={wide} />)).toMatch(/Control trades about 287 times as often a session/);
  });

  it("the lightbox: plain words first, a flagged count on health, one arm's months, settings, and the full report", () => {
    const summary = body("summary");
    expect(summary).toContain("in plain English");
    expect(summary).toContain("Each arm against control");
    // The full report is folded in as tabs, with the amber licensed-data flag; no page of its own.
    expect(summary).not.toContain("full=1");
    expect(summary).toContain("licensed vendor data");
    for (const t of ["Arms", "Paired with control", "Run cards", "Refused"]) expect(summary).toContain(t);
    // Health reads the first non-control arm by default; its flags count on the tab.
    const health = body("health", REPORT, "d12");
    expect(health).toContain("2 flagged");
    expect(health).toContain('aria-pressed="true">d12<');
    expect(body("calendar")).toContain("<td>2024</td>");
    const settings = body("settings");
    expect(settings).toContain("abc1234");
    expect(settings).toContain("fp-1");
    // While the report loads, the body says so.
    expect(body("summary", null)).toContain("loading the report");
  });
});
