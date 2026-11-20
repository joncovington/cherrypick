import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { GridResult, GridRow, Grids } from "@console/shared";
import { GridList, GridView, gridFailedChecks, gridRowState } from "../src/pages/Builder/BuilderGrid";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!-- -->/g, "");

const GATES = { gains: true, beats_menu: true, plateau: true, stable: true, pays_at_1_5x: true };

function row(over: Partial<GridRow> = {}): GridRow {
  return {
    id: "profit_target_pct=50",
    axis: "profit_target_pct",
    setting: "a profit target at 50% of max profit",
    moved: 80,
    scored: true,
    mean_change: 4.2,
    change_ci: [1.1, 7.3],
    gain: 672,
    gain_shrunk: 210,
    z: 3.4,
    p: 0.001,
    p_menu: 0.02,
    holm: true,
    bh: true,
    plateau: { verdict: "plateau" },
    stability: { folds_positive: 4, stable: true },
    cost: { mean_change_at_1_5x: 2.0 },
    held_out: { planned_sessions: 80, mde: 3.0, testable: true },
    gates: GATES,
    survives: true,
    declaration: null,
    held_out_read: null,
    ...over,
  };
}

const GRID: GridResult = {
  name: "grid-mine-2611181000",
  automation: "mine",
  run: "builder-mine-2611161730",
  symbol: "SPX",
  source: "x",
  sessions: ["2025-11-17", "2026-11-17"],
  split: "2026-07-18",
  base: { sessions: 160, trades: 160, net: 100, held_out_sessions: 80 },
  tried: 13,
  survivors: 1,
  holm_survivors: 1,
  bh_survivors: 1,
  base_matches_run: true,
  generated_at: "2026-11-18T10:00:00",
  licensed: false,
  declared: [],
  held_out_used: 0,
  draws: 2000,
  null_max_median: 2.6,
  refused: { "close_before_expiry_min=off": "a far leg needs a close before expiry" },
  rows: [
    row(),
    row({ id: "width=x2", setting: "the width at 2x", survives: false, gates: { ...GATES, plateau: false, pays_at_1_5x: false } }),
    { id: "stop_debit_pct=25", axis: "stop_debit_pct", setting: "a stop at 25% of the debit", moved: 0, scored: false, why: "changes no session", survives: false, declaration: null, held_out_read: null },
  ],
  held_out_uses: [],
};

describe("the settings grid", () => {
  it("offers Declare on a testable survivor, then the one test, then the verdict", () => {
    expect(gridRowState(row())).toBe("declare");
    expect(gridRowState(row({ held_out: { planned_sessions: 80, mde: 9, testable: false } }))).toBe("untestable");
    expect(gridRowState(row({ declaration: { status: "declared" } }))).toBe("test");
    const read = { setting: "x", at: "t", use: 1, sessions: 80, moved: 40, mean_change: 3, gain: 240, ci: [0.5, 5.5] as [number, number], verdict: "passed" as const };
    expect(gridRowState(row({ declaration: {}, held_out_read: read }))).toBe("tested");
  });

  it("says why a setting failed, or why it was not scored", () => {
    expect(gridFailedChecks(GRID.rows[1]!)).toEqual(["a lone peak: its neighbouring settings disagree", "the gain is gone at 1.5x costs"]);
    expect(gridFailedChecks(GRID.rows[2]!)).toEqual(["changes no session"]);
  });

  it("names the split, what was not read, the refused and unscored settings", () => {
    const out = html(<GridView grid={GRID} />);
    expect(out).toContain("13 settings scored on 160 sessions before 2026-07-18");
    expect(out).toContain("The later 80 sessions were not read");
    expect(out).toContain("a stop at 25% of the debit (changes no session)");
    expect(out).toContain("close_before_expiry_min=off (refused: a far leg needs a close before expiry)");
    expect(out).toContain("Declare");
    expect(out).not.toContain("did not give the run");
    expect(html(<GridView grid={{ ...GRID, base_matches_run: false }} />)).toContain("did not give the run&#x27;s own trades again");
  });

  it("lists a live grid as queue work and the finished ones by their stamp", () => {
    const data: Grids = {
      name: "mine",
      tasks: [{ id: 9, grid: "grid-mine-2611190900", status: "running", queued_at: null, finished_at: null, note: null }],
      grids: [GRID],
      withheld: 1,
      results_unavailable: false,
    };
    const out = html(<GridList data={data} onOpen={() => {}} />);
    expect(out).toContain("task 9 · grid-mine-2611190900 · running · each setting is a full replay");
    expect(out).toContain("13 settings scored before 2026-07-18, 1 survives");
    expect(out).toContain("1 licensed grid(s) not shown.");
  });
});
