import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { BacktestOverview, BacktestRunReport, StudiesBoard, StudyRow } from "@console/shared";
import { RunPlainEnglish } from "../src/pages/Backtest/BacktestPage";
import { StudyChip, etMs } from "../src/pages/Backtest/QueueTab";
import { StudiesTable, StudyDetail, contextLine, headline, planUrl, resultLabel, resultsLine } from "../src/pages/Backtest/StudiesTab";
import { indexStudies, studyHref } from "../src/pages/Backtest/studyIndex";

/**
 * The Backtest page's studies tab (2026-10-29): one row per register item, grouped in progress /
 * declared / finished / observations. Every word is the orchestrator's; the tab lays it out.
 */

const NOW = etMs("2026-10-29T12:00:00") as number;

function row(over: Partial<StudyRow>): StudyRow {
  return {
    id: 23,
    study: "item23",
    title: "The short delta of MEIC's live rule",
    status: "failed",
    legacy: false,
    pointer: "docs/edge-hypotheses-plan.md#23-the-short-delta",
    outcome: "live stays at 16 delta",
    measure: "net per session",
    expected: "a positive paired difference",
    sample: "design 2022-05-16..2025-06-30",
    rule: "t > 2, Holm, both halves",
    mechanism: "a nearer short collects more credit",
    rivals: [{ explanation: "a calm span", check: "both halves positive" }],
    group: "finished",
    results: [
      {
        name: "study-design.json",
        present: true,
        modified: "2026-10-28T06:50:12",
        window: "design",
        span: null,
        runId: "20261028-065012-0174bd",
        provisional: true,
        verdicts: [
          { arm: "d12", verdict: "fail" },
          { arm: "d20", verdict: "fail" },
        ],
        summary: "fail",
      },
      {
        name: "study-holdout.json",
        present: true,
        modified: "2026-10-28T17:30:06",
        window: "holdout",
        span: null,
        runId: null,
        provisional: true,
        verdicts: [{ arm: null, verdict: "fail" }],
        summary: "fail",
      },
    ],
    queueActive: false,
    tasks: [{ id: 24, step: "holdout", status: "done", createdAt: null, startedAt: null, finishedAt: "2026-10-28T23:38:59" }],
    lastActivity: "2026-10-28T23:38:59",
    ...over,
  };
}

const BOARD: StudiesBoard = {
  unregistered: [],
  rows: [
    row({ id: 26, study: "item26", title: "A dip-buying share ladder", status: "declared", group: "in_progress", queueActive: true, results: [], outcome: null }),
    row({}),
    row({ id: 25, study: "item25", status: "no_verdict", results: [] }),
    row({ id: 2, study: "item2", status: "observation", group: "observation", legacy: true, results: [], rivals: [], rule: null }),
  ],
};

const html = (node: React.ReactNode) => renderToString(<MemoryRouter>{node}</MemoryRouter>);

describe("studies tab", () => {
  it("heads with the counts the user asks for first", () => {
    expect(headline(BOARD)).toBe("4 studies: 1 in progress, 0 declared, 2 finished (1 failed, 1 no verdict), 1 observation.");
  });

  it("groups rows and shows status, verdicts and outcome in one line each", () => {
    const out = html(<StudiesTable board={BOARD} nowMs={NOW} />);
    const order = ["In progress", "Finished", "Observations"].map((t) => out.indexOf(t));
    expect(order.every((i, k) => i > -1 && (k === 0 || i > order[k - 1]!))).toBe(true);
    expect(out).not.toContain("Declared, not started");
    expect(out).toContain("design fail · holdout fail");
    expect(out).toContain("live stays at 16 delta");
    expect(out).toContain("task running");
    expect(out).toContain("no verdict");
  });

  it("a result with no word says so, never a guess; a missing file says missing", () => {
    const r = row({}).results[0]!;
    expect(resultsLine(row({ results: [{ ...r, verdicts: null, summary: null }] }))).toBe("design see plan");
    expect(resultsLine(row({ results: [{ ...r, present: false, summary: null }] }))).toBe("design missing");
    expect(resultLabel({ ...r, window: null, span: ["2022-05-16", "2024-12-31"] })).toBe("2022–2024");
    expect(resultLabel({ ...r, window: null, span: null })).toBe("study-design.json");
  });

  it("a row opens to its test, rivals, per-arm verdicts and links", () => {
    const out = html(<StudyDetail row={row({})} />);
    expect(out).toContain("t &gt; 2, Holm, both halves");
    expect(out).toContain("a calm span");
    expect(out).toContain("d12: fail · d20: fail");
    expect(out).toContain('href="/backtest/runs?run=20261028-065012-0174bd"');
    expect(out).toContain(planUrl("docs/edge-hypotheses-plan.md#23-the-short-delta")!);
    expect(out).toContain("its queue rows");
    // A pre-register item points to its prose.
    expect(html(<StudyDetail row={row({ legacy: true, rule: null, rivals: [] })} />)).toContain("before the register");
  });

  it("says why replays are provisional only while validation has not unlocked", () => {
    const o = {
      windows: { design: ["2022-05-16", "2025-06-30"], holdout: ["2025-07-01", "2026-10-12"] },
      validation: [{ unlocks_history: false }],
    } as unknown as BacktestOverview;
    expect(contextLine(o)).toContain("provisional");
    expect(contextLine(o)).toContain("Design window 2022-05-16 to 2025-06-30");
    expect(contextLine({ ...o, validation: [{ unlocks_history: true }] } as unknown as BacktestOverview)).not.toContain("provisional");
    expect(contextLine(null)).toBeNull();
  });
});

describe("links between the tabs", () => {
  const index = indexStudies(BOARD);

  it("indexes a study by name and a run by the result that names it", () => {
    expect(index.byStudy.get("item25")?.status).toBe("no_verdict");
    expect(index.byRun.get("20261028-065012-0174bd")?.result.window).toBe("design");
    expect(indexStudies(null).byRun.size).toBe(0);
  });

  it("a queue row shows its study's verdict, linking to the board; a declared study shows nothing", () => {
    const failed = html(<StudyChip study="item23" studies={index} />);
    expect(failed).toContain("failed");
    expect(failed).toContain(`href="${studyHref("item23")}"`);
    expect(html(<StudyChip study="item26" studies={index} />)).toBe("");
    expect(html(<StudyChip study="validation" studies={index} />)).toBe("");
  });

  it("a study named in the URL opens", () => {
    expect(html(<StudiesTable board={BOARD} nowMs={NOW} focus="item23" />)).toContain("pass test");
    expect(html(<StudiesTable board={BOARD} nowMs={NOW} />)).not.toContain("pass test");
  });

  it("a run report opens in plain words: the study, its verdict, each arm against control", () => {
    const r = {
      window: "design",
      span: ["2022-05-16", "2025-06-30"],
      cadence: 60,
      arms: {
        control: { spec_hash: "c", summary: { sessions: 778, total: 1000, mean: 1.29 } },
        d20: { spec_hash: "a", summary: { sessions: 778 }, vs_control: { sessions: 778, mean_diff: 2.5, ci: [-1, 6] } },
        d12: { spec_hash: "b", summary: { sessions: 778 }, vs_control: { sessions: 778, mean_diff: -3, ci: [-6, -0.5] } },
        same: { spec_hash: "d", summary: { sessions: 778 }, no_exposure: true },
      },
    } as unknown as BacktestRunReport;
    const study = index.byRun.get("20261028-065012-0174bd");
    const out = html(<RunPlainEnglish r={r} study={study} />).replace(/<!-- -->/g, "");
    expect(out).toContain("item 23");
    expect(out).toContain("<strong>fail</strong>");
    expect(out).toContain("live stays at 16 delta");
    expect(out).toContain("d20: +$2.50 a session against control; the interval includes zero");
    expect(out).toContain("the interval is below zero");
    expect(out).toContain("same measured nothing");
    expect(html(<RunPlainEnglish r={r} />)).toContain("No study in the register names this run.");
  });
});
