import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { IdeaRow, IdeaSearch, IdeaSearches } from "@console/shared";
import { failedChecks, IdeaSearchList, IdeaSearchView, rowState } from "../src/pages/Builder/BuilderIdeas";

const html = (el: React.ReactElement) =>
  renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const PASS = { loses: true, beats_menu: true, plateau: true, stable: true, adds: true, pays_at_1_5x: true };

function row(over: Partial<IdeaRow> = {}): IdeaRow {
  return {
    id: "vix:above:3",
    idea: "skip VIX at the prior close 25 and over",
    conditions: ["VIX at the prior close 25 and over"],
    skipped: 40,
    skipped_share: 0.25,
    skipped_mean: -120,
    skipped_ci: [-160, -80],
    uplift: 4800,
    uplift_shrunk: 2100,
    z: -6.1,
    p: 0.001,
    p_menu: 0.004,
    holm: true,
    bh: true,
    plateau: { verdict: "plateau", axes: [] },
    stability: { folds_negative: 4, stable: true },
    adds: { adds: true },
    cost: { mean_by_multiplier: { "1.0": 12, "1.5": 8 }, survives: true },
    held_out: { planned_sessions: 20, mde: 60, testable: true },
    gates: { ...PASS },
    survives: true,
    declaration: null,
    held_out_read: null,
    ...over,
  };
}

function search(rows: IdeaRow[]): IdeaSearch {
  return {
    name: "ideas-mine-2611181000",
    automation: "mine",
    run: "builder-mine-2611161730",
    symbol: "SPX",
    source: "recorded-0dte",
    sessions: ["2025-11-17", "2026-11-16"],
    split: "2026-07-18",
    base: { sessions: 165, trades: 165, net: 900, mean_session: 5.4, held_out_sessions: 85 },
    tried: 147,
    survivors: rows.filter((r) => r.survives).length,
    holm_survivors: 1,
    bh_survivors: 1,
    generated_at: "2026-11-18T10:00:00-05:00",
    licensed: false,
    declared: rows.filter((r) => r.declaration).map((r) => r.id),
    held_out_used: rows.filter((r) => r.held_out_read).length,
    draws: 2000,
    null_min_median: -3.1,
    rules: {},
    rows,
    held_out_uses: [],
  };
}

describe("a row's state", () => {
  it("is declare, then test, then tested; untestable and failed say why", () => {
    expect(rowState(row())).toBe("declare");
    expect(rowState(row({ declaration: { status: "declared" } }))).toBe("test");
    const read = { idea: "vix:above:3", at: "t", use: 1, sessions: 85, skipped: 21, skipped_mean: -90, uplift: 1890, ci: [-130, -50] as [number, number], verdict: "passed" as const };
    expect(rowState(row({ declaration: {}, held_out_read: read }))).toBe("tested");
    expect(rowState(row({ held_out: { planned_sessions: 4, mde: 300, testable: false } }))).toBe("untestable");
    const lone = row({ survives: false, gates: { ...PASS, plateau: false, beats_menu: false } });
    expect(rowState(lone)).toBe("failed");
    expect(failedChecks(lone)).toEqual(["no better than the best random filter", "a lone peak: its neighbouring bins disagree"]);
  });
});

describe("the idea search view", () => {
  it("says how many were tried, that the later part was not read, and offers Declare on a survivor", () => {
    const out = html(<IdeaSearchView search={search([row(), row({ id: "weekday:bin:0", idea: "skip a Mon", survives: false, gates: { ...PASS, loses: false } })])} />);
    expect(out).toContain("147 filters tried on 165 sessions before 2026-07-18");
    expect(out).toContain("The later 85 sessions were not read");
    expect(out).toContain("Holm keeps 1, BH keeps 1 of the 147");
    expect(out).toContain(">Declare</button>");
    expect(out).toContain("$2100.00</span> after the search");
    expect(out).toContain("the skipped days did not plainly lose");
  });

  it("offers the one held-out test once declared, shows the draft row, then the verdict", () => {
    const declared = row({ declaration: { status: "declared", rule: "on the sessions from 2026-07-18" } });
    const out = html(<IdeaSearchView search={search([declared])} />);
    expect(out).toContain("Test once on held-out");
    expect(out).toContain("Register row draft");
    expect(out).toContain("on the sessions from 2026-07-18");
    const read = { idea: "vix:above:3", at: "t", use: 1, sessions: 85, skipped: 21, skipped_mean: -90, uplift: 1890, ci: [-130, -50] as [number, number], verdict: "failed" as const };
    const tested = html(<IdeaSearchView search={search([{ ...declared, held_out_read: read }])} />);
    expect(tested).toContain("Held-out: failed");
    expect(tested).not.toContain("Test once on held-out");
  });
});

describe("the searches list", () => {
  it("lists live tasks, finished searches and what is withheld", () => {
    const data: IdeaSearches = {
      name: "mine",
      tasks: [{ id: 512, search: "ideas-mine-2611181000", status: "running", queued_at: null, finished_at: null, note: null }],
      searches: [{ ...search([row()]), held_out_used: 2 }],
      withheld: 1,
      results_unavailable: false,
    };
    const out = html(<IdeaSearchList data={data} onOpen={() => {}} />);
    expect(out).toContain("task 512 · ideas-mine-2611181000 · running");
    expect(out).toContain("147 tried before 2026-07-18, 1 survives");
    expect(out).toContain("held-out part used 2×");
    expect(out).toContain("1 licensed search(es) not shown.");
  });
});
