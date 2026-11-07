import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BacktestOverview, BacktestRunReport, StudiesBoard, StudyGrid, StudyResult, StudyRow, StudyRunFigures, StudyRunSummaries } from "@console/shared";
import { RunPlainEnglish } from "../src/pages/Backtest/BacktestPage";
import { neighbourhoodLine } from "../src/pages/Backtest/GridBars";
import { StudyChip, etMs } from "../src/pages/Backtest/QueueTab";
import { BacktestStudiesTab, StudyNotes, contextLine, headline, historyLocked, windowsLine, notesHref, planUrl, resultLabel, resultsLine } from "../src/pages/Backtest/StudiesTab";
import {
  type DialogTab,
  ArmFigures,
  EquityLines,
  StudyDialogBody,
  StudyTiles,
  Verdicts,
  armsLine,
  chipLabel,
  controlScale,
  figuresRun,
  figuresRunIds,
  heroStudy,
  laidOut,
  openIndex,
  orderedRows,
  tileFigures,
  tileModel,
  tradesPerSession,
  verdictText,
} from "../src/pages/Backtest/StudyCards";
import { indexStudies, studyHref } from "../src/pages/Backtest/studyIndex";

/**
 * The Backtest page's studies tab (2026-10-29; as cards 2026-11-03): one tile per register item, in
 * progress first, opening to a lightbox. Every word is the orchestrator's or the study's; the tab
 * lays it out and works out no verdict.
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
        source: "register",
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
        grid: null,
        notes: null,
      },
      {
        source: "register",
        name: "study-holdout.json",
        present: true,
        modified: "2026-10-28T17:30:06",
        window: "holdout",
        span: null,
        runId: null,
        provisional: true,
        verdicts: [{ arm: null, verdict: "fail" }],
        summary: "fail",
        grid: null,
        notes: null,
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
    row({
      id: 26,
      study: "item26",
      title: "A dip-buying share ladder",
      status: "declared",
      group: "in_progress",
      queueActive: true,
      tasks: [{ id: 40, step: "fetch", status: "running", createdAt: null, startedAt: "2026-10-28T19:00:00", finishedAt: null }],
      results: [],
      outcome: null,
    }),
    row({}),
    row({ id: 25, study: "item25", status: "no_verdict", results: [] }),
    row({ id: 2, study: "item2", status: "observation", group: "observation", legacy: true, results: [], rivals: [], rule: null }),
  ],
};

const html = (node: React.ReactNode) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

const tiles = (rows: StudyRow[]) => html(<StudyTiles rows={rows} nowMs={NOW} onOpen={() => undefined} />);

const dialog = (r: StudyRow, tab: DialogTab = "overview") =>
  html(
    <StudyDialogBody
      row={r}
      tab={tab}
      onTab={() => undefined}
      position="1 of 1"
      nowMs={NOW}
      onPrev={() => undefined}
      onNext={() => undefined}
      onClose={() => undefined}
    />,
  );

function result(over: Partial<StudyResult>): StudyResult {
  return {
    source: "register",
    name: "results.json",
    present: true,
    modified: null,
    window: null,
    span: null,
    runId: null,
    provisional: null,
    verdicts: [{ arm: null, verdict: "fail" }],
    summary: "fail",
    grid: null,
    notes: null,
    version: null,
    ...over,
  };
}

describe("studies tab", () => {
  it("heads with the counts the user asks for first", () => {
    expect(headline(BOARD)).toBe("4 studies: 1 in progress, 0 declared, 2 finished (1 failed, 1 no verdict), 1 observation.");
    // A study retired unrun is finished, counted by its own word.
    const withRetired = { ...BOARD, rows: [...BOARD.rows, row({ id: 11, study: "item11", status: "retired", results: [] })] };
    expect(headline(withRetired)).toBe("5 studies: 1 in progress, 0 declared, 3 finished (1 failed, 1 no verdict, 1 retired), 1 observation.");
  });

  it("tiles run in progress first, then finished, then observations; the decision waits for the lightbox", () => {
    expect(orderedRows(BOARD, null).map((r) => r.study)).toEqual(["item26", "item23", "item25", "item2"]);
    expect(orderedRows(BOARD, "finished").map((r) => r.study)).toEqual(["item23", "item25"]);
    const out = tiles(orderedRows(BOARD, null));
    const at = (s: string) => out.indexOf(`data-study="${s}"`);
    expect(at("item26")).toBeGreaterThan(-1);
    expect(at("item26")).toBeLessThan(at("item23"));
    expect(at("item23")).toBeLessThan(at("item2"));
    expect(out).toContain("fetch running");
    expect(out).toContain("no verdict");
    expect(out).toContain('class="study-tile-word tone-neg">failed<');
    expect(out).not.toContain("live stays at 16 delta");
    const opened = dialog(row({}));
    expect(opened).toContain(">design fail<");
    expect(opened).toContain("live stays at 16 delta");
  });

  it("a study in progress shows its step and how far through; one only queued says queued, not running", () => {
    // The queue's `active` is queued or running (2026-10-30: two studies said "task running" in
    // the 18:30-20:30 window while the queue ran nothing).
    const waiting = row({
      id: 30,
      study: "item30",
      status: "declared",
      group: "in_progress",
      queueActive: true,
      tasks: [
        { id: 41, step: "fetch", status: "done", createdAt: null, startedAt: null, finishedAt: "2026-10-28T10:00:00" },
        { id: 42, step: "fetch-chain-paths", status: "queued", createdAt: null, startedAt: null, finishedAt: null },
        { id: 43, step: "old", status: "cancelled", createdAt: null, startedAt: null, finishedAt: null },
      ],
      results: [],
      outcome: null,
    });
    const m = tileModel(waiting);
    expect([m.kind, m.step, m.done, m.total]).toEqual(["running", "fetch-chain-paths queued", 1, 2]);
    const out = tiles([waiting]);
    expect(out).toContain("1 of 2 steps done");
    expect(out.match(/study-segment is-done/g)).toHaveLength(1);
    expect(out.match(/study-segment is-queued/g)).toHaveLength(1);
    expect(out).not.toContain(" running<");
  });

  it("a declared tile shows what it expects instead of a verdict", () => {
    const declared = row({ group: "declared", status: "declared", results: [], tasks: [] });
    const out = tiles([declared]);
    expect(out).toContain(">Declared<");
    expect(out).toContain("a positive paired difference");
    expect(out).not.toContain("study-tile-word");
  });

  it("marks a stale or provisional result on the tile, and says how the arms split when they disagree", () => {
    const stale = result({
      window: "holdout",
      provisional: true,
      verdicts: [
        { arm: "w15", verdict: "fail" },
        { arm: "w20", verdict: "pass" },
        { arm: "w25", verdict: "pass" },
      ],
      summary: "mixed",
      version: { version: 2, current: 3, commit: null, stale: true, since: [{ on: "2026-10-30", what: "fees per leg", beforeRead: true }], error: null },
    });
    const m = tileModel(row({ results: [stale] }));
    expect(m.stale).toBe(true);
    expect(m.meta).toBe("holdout · provisional");
    expect(m.arms).toBe("2 pass · 1 fail");
    expect(armsLine(result({ verdicts: [{ arm: "a", verdict: "fail" }, { arm: "b", verdict: "fail" }] }))).toBeNull();
    expect(armsLine(null)).toBeNull();
    const out = tiles([row({ results: [stale] })]);
    expect(out).toContain(">stale<");
    expect(out).toContain("2 pass · 1 fail");
    expect(tiles([row({})])).not.toContain(">stale<");
    // The lightbox says what landed since, and to rerun.
    const opened = dialog(row({ results: [stale] }));
    expect(opened).toContain("Rerun before relying on the verdict");
    expect(opened).toContain("2026-10-30: fees per leg");
  });

  it("a result with no word says so, never a guess; a missing file says missing", () => {
    const r = row({}).results[0]!;
    expect(resultsLine(row({ results: [{ ...r, verdicts: null, summary: null }] }))).toBe("design see plan");
    expect(resultsLine(row({ results: [{ ...r, present: false, summary: null }] }))).toBe("design missing");
    expect(resultLabel({ ...r, window: null, span: ["2022-05-16", "2024-12-31"] })).toBe("2022–2024");
    expect(resultLabel({ ...r, window: null, span: null })).toBe("study-design.json");
  });

  it("the large tile is the study running now, laid out first; none when nothing runs", () => {
    const rows = orderedRows(BOARD, null);
    expect(heroStudy(rows)?.study).toBe("item26");
    expect(tiles(rows)).toContain('class="study-tile study-hero" data-study="item26"');
    expect(tiles(rows)).toContain("Running now · fetch running");
    const finished = orderedRows(BOARD, "finished");
    expect(heroStudy(finished)).toBeNull();
    expect(tiles(finished)).not.toContain("study-hero");
    const reordered = [row({}), ...rows.filter((r) => r.study !== "item23")];
    expect(laidOut(reordered).map((r) => r.study)).toEqual(["item26", "item23", "item25", "item2"]);
  });

  it("the lightbox's tabs: the decision and run on the overview, the test and rivals, the grid, the queue", () => {
    const overview = dialog(row({}));
    expect(overview).toContain("live stays at 16 delta");
    expect(overview).toContain('href="/backtest/runs?run=20261028-065012-0174bd"');
    expect(overview).toContain(`href="${studyHref("item23")}"`);
    expect(overview).toContain('aria-selected="true"');
    expect(overview).not.toContain("a calm span");
    const declaration = dialog(row({}), "declaration");
    expect(declaration).toContain("t &gt; 2, Holm, both halves");
    expect(declaration).toContain("a calm span");
    expect(declaration).toContain(planUrl("docs/edge-hypotheses-plan.md#23-the-short-delta")!);
    const queue = dialog(row({}), "queue");
    expect(queue).toContain("d12: fail · d20: fail");
    expect(queue).toContain("holdout done");
    expect(dialog(row({}), "grid")).toContain("No grid");
    // A pre-register item points to its prose; a declared one says it has not run.
    expect(dialog(row({ legacy: true, rule: null, rivals: [] }), "declaration")).toContain("before the register");
    expect(dialog(row({ group: "declared", status: "declared", results: [] }))).toContain("Declared, not run yet.");
  });

  it("a tile draws its run in brief: a line per arm, the highest mean named, control left off on another scale", () => {
    const RUN = "20261028-065012-0174bd";
    const run = (controlTrades: number): StudyRunFigures => ({
      module: "meic",
      window: "design",
      provisional: true,
      arms: {
        control: { mean: 80, sessions: 10, trades: controlTrades, path: [0, 900, -800] },
        d16: { mean: -12, sessions: 10, trades: 10, path: [0, -5, -9] },
        d20: { mean: -4.5, sessions: 10, trades: 9, path: [0, -2, -4] },
      },
    });
    // Control is the module's sampling arm: left off the line, and out of "best".
    const wide = tileFigures(run(2990))!;
    expect(wide.controlLeftOff).toBe(true);
    expect(wide.lines.map((l) => l.arm)).toEqual(["d16", "d20"]);
    expect([wide.lead, wide.armsWithMean]).toEqual([{ arm: "d20", mean: -4.5 }, 2]);
    // On the same scale control is drawn, dashed, and still not the arm named.
    const same = tileFigures(run(10))!;
    expect(same.lines.map((l) => l.arm)).toEqual(["control", "d16", "d20"]);
    expect(same.lead?.arm).toBe("d20");
    // A run with control alone names control.
    expect(tileFigures({ ...run(10), arms: { control: run(10).arms["control"]! } })?.lead?.arm).toBe("control");
    expect(tileFigures({ ...run(10), arms: {} })).toBeNull();
    expect(figuresRunIds([row({}), row({ study: "x" }), row({ study: "y", results: [] })])).toEqual([RUN]);
    // On the tile: the line, the figure in the wording the user chose, and a shorter title.
    const summaries: StudyRunSummaries = { runs: { [RUN]: run(2990) }, missing: [] };
    const out = html(<StudyTiles rows={[row({})]} nowMs={NOW} onOpen={() => undefined} summaries={summaries} />);
    expect(out).toContain("study-tile has-figures");
    expect(out.match(/<polyline /g)).toHaveLength(2);
    expect(out).toContain("<span>-$4.50</span> <span>/ session</span>");
    expect(out).toContain("best of 2");
    // No run in brief: the tile is as it was.
    expect(tiles([row({})])).not.toContain("has-figures");
  });

  it("takes its figures from the newest result file that names a run", () => {
    const [design, holdout] = [result({ name: "d.json", runId: "20261001-000000-aaaaaa", modified: "2026-10-01T00:00:00" }), result({ name: "h.json", runId: "20261020-000000-bbbbbb", modified: "2026-10-20T00:00:00" })];
    expect(figuresRun(row({ results: [design, holdout] }))?.name).toBe("h.json");
    expect(figuresRun(row({ results: [design, { ...holdout, present: false }] }))?.name).toBe("d.json");
    expect(figuresRun(row({ results: [result({})] }))).toBeNull();
    // A study script's own label names no runner report: passed over for an older file that does.
    const label = result({ name: "s.json", runId: "item30-2026-10-30", modified: "2026-10-30T00:00:00" });
    expect(figuresRun(row({ results: [design, label] }))?.name).toBe("d.json");
    expect(figuresRun(row({ results: [label] }))).toBeNull();
  });

  it("shows each arm's net a session and its verdict, control first", () => {
    const report = {
      arms: {
        d20: { spec_hash: "a", summary: { sessions: 778, mean: -2.5 } },
        control: { spec_hash: "c", summary: { sessions: 778, mean: 1.29 } },
      },
    } as unknown as BacktestRunReport;
    const out = html(<ArmFigures report={report} result={result({ verdicts: [{ arm: "d20", verdict: "fail" }] })} />);
    expect(out.indexOf("control")).toBeLessThan(out.indexOf("d20"));
    expect(out).toContain("+$1.29 / session");
    expect(out).toContain("-$2.50 / session");
    expect(out).toMatch(/class="chip chip-missing verdict-fail">fail</);
  });

  it("starts control hidden only when it trades on another scale from the study's arms, and says why", () => {
    const eq = (cums: number[]) => cums.map((cum, i) => ({ session: `2026-01-0${String(i + 1)}`, net: 0, cum, drawdown: 0 }));
    const report = (controlTrades: number) =>
      ({
        arms: {
          control: { spec_hash: "c", summary: { sessions: 10, mean: 80 }, trades: controlTrades },
          d16: { spec_hash: "a", summary: { sessions: 10, mean: 12 }, trades: 10 },
          d20: { spec_hash: "b", summary: { sessions: 10, mean: 15 }, trades: 9 },
        },
        cards: { ok: true, arms: { control: { equity: eq([0, 900, -800]) }, d16: { equity: eq([0, 5, 9]) }, d20: { equity: eq([0, 6, 12]) } } },
      }) as unknown as BacktestRunReport;
    // MEIC's sampling arm: hundreds of condors a session against one.
    const wide = report(2990);
    expect(tradesPerSession(wide, "control")).toBe(299);
    expect(Math.round(controlScale(wide)!)).toBe(299);
    const hidden = html(<EquityLines report={wide} />);
    expect(hidden.match(/<polyline /g)).toHaveLength(2);
    expect(hidden).toContain("Control starts hidden");
    expect(hidden).toContain('aria-pressed="false"');
    // Control on the same scale is the comparison, and starts shown.
    const same = report(10);
    expect(controlScale(same)).toBeNull();
    const shown = html(<EquityLines report={same} />);
    expect(shown.match(/<polyline /g)).toHaveLength(3);
    expect(shown).not.toContain("Control starts hidden");
    // No trade counts, no claim either way.
    expect(controlScale({ arms: { control: { spec_hash: "c", summary: { sessions: 10 } } } } as unknown as BacktestRunReport)).toBeNull();
    // The arm table says how often each arm traded.
    expect(html(<ArmFigures report={wide} result={result({})} />)).toContain("10 sessions · 299 trades / session");
    expect(html(<ArmFigures report={wide} result={result({})} />)).toContain("10 sessions · 0.9 trades / session");
  });

  it("draws each arm's running net, control dashed, and says when the report has no path", () => {
    const eq = (cums: number[]) => cums.map((cum, i) => ({ session: `2026-01-0${String(i + 1)}`, net: 0, cum, drawdown: 0 }));
    const report = { arms: {}, cards: { ok: true, arms: { control: { equity: eq([0, 1, 2]) }, d20: { equity: eq([0, -1, -3]) } } } } as unknown as BacktestRunReport;
    const out = html(<EquityLines report={report} />);
    expect(out.match(/<polyline /g)).toHaveLength(2);
    expect(out).toContain('stroke-dasharray="6 4"');
    expect(out.match(/aria-pressed="true"/g)).toHaveLength(2);
    expect(html(<EquityLines report={{ arms: {} } as unknown as BacktestRunReport} />)).toContain("no equity path");
  });

  it("a grid result draws a bar per setting with the live one outlined, and the study's own labels", () => {
    const grid: StudyGrid = {
      parameter: "short delta",
      measure: "mean net per session",
      declared: "d16",
      rows: [
        { arm: "d12", setting: 0.12, label: "0.12", mean: 4, n: 120, verdict: "fail", declared: false },
        { arm: "d16", setting: 0.16, label: "0.16", mean: 5, n: 120, verdict: null, declared: true },
        { arm: "d20", setting: 0.2, label: "0.2", mean: -2, n: 120, verdict: "pass", declared: false },
      ],
      neighbourhood: { d16: "plateau", d20: "not_positive" },
    };
    expect(neighbourhoodLine(grid)).toBe("live 0.16: on a plateau · 0.2 (pass): not positive");
    const r = row({}).results[0]!;
    const out = dialog(row({ results: [{ ...r, grid }] }), "grid");
    expect(out.match(/<rect /g)).toHaveLength(3);
    expect(out).toContain("short delta 0.16 (live)");
    expect(out).toContain("var(--neg)");
    expect(dialog(row({}), "grid")).not.toContain("<rect");
  });

  it("the lightbox links to the study's Markdown beside its result file; none, no link", () => {
    const r = row({}).results[0]!;
    const out = dialog(row({ results: [{ ...r, notes: "# x" }] }), "queue");
    expect(out).toContain(">study-design.md</a>");
    expect(out).toContain(`href="${notesHref("item23", "study-design.json").replace(/&/g, "&amp;")}"`);
    expect(dialog(row({}), "queue")).not.toContain("study-design.md");
  });

  it("the write-up opens full width, as elements, never as HTML, with the way back", () => {
    const r = row({}).results[0]!;
    const md = "# Design\n\n| arm | win % | P&L |\n|---|---|---|\n| d12 | 45% | −$3,221 |\n\n- **close** loses\n\n<script>x</script>";
    const board: StudiesBoard = { unregistered: [], rows: [row({ results: [{ ...r, notes: md }] })] };
    const out = html(<StudyNotes board={board} study="item23" resultName="study-design.json" />);
    expect(out).toContain("<h4>Design</h4>");
    expect(out).toContain('<td class="num pnl-neg">−$3,221</td>');
    expect(out).toContain('<td class="num">45%</td>');
    expect(out).toContain("<strong>close</strong>");
    expect(out).not.toContain("<script>");
    expect(out).toContain(`href="${studyHref("item23")}"`);
    expect(html(<StudyNotes board={board} study="item23" resultName="gone.json" />).replace(/<!-- -->/g, "")).toContain("No gone.md for item23");
  });

  it("the studies card keeps the provisional line behind an amber flag and its footer behind the ?", () => {
    const o = {
      windows: { design: ["2022-05-16", "2025-06-30"], holdout: ["2025-07-01", "2026-10-12"] },
      validation: [{ unlocks_history: false }],
    } as unknown as BacktestOverview;
    const page = (ov: BacktestOverview) =>
      renderToString(
        (() => {
          const c = new QueryClient();
          c.setQueryData(["bridged", "backtest", "studies"], { ok: true, data: BOARD, error: null });
          c.setQueryData(["bridged", "backtest", "overview"], { ok: true, data: ov, error: null });
          return (
            <QueryClientProvider client={c}>
              <MemoryRouter>
                <BacktestStudiesTab />
              </MemoryRouter>
            </QueryClientProvider>
          );
        })(),
      ).replace(/<!-- -->/g, "");
    const locked = page(o);
    expect(locked).toContain('class="help-flag"');
    expect(locked).toContain("provisional history");
    expect(locked).toContain("Design window 2022-05-16 to 2025-06-30");
    // Neither the provisional sentence nor the register footer is on the page itself.
    expect(locked).not.toContain("replays are provisional");
    expect(locked).not.toContain("One card per item in the hypothesis register");
    expect(page({ ...o, validation: [{ unlocks_history: true }] } as unknown as BacktestOverview)).not.toContain("provisional history");
    expect(historyLocked(o)).toBe(true);
    expect(windowsLine(null)).toBeNull();
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

  it("a study named in the URL opens, stepping through its group, or every study when filtered out", () => {
    expect(openIndex(BOARD, null, "item23")).toMatchObject({ index: 1 });
    expect(openIndex(BOARD, "finished", "item25")).toMatchObject({ index: 1 });
    const out = openIndex(BOARD, "finished", "item26");
    expect([out.rows.length, out.index]).toEqual([4, 0]);
    expect(openIndex(BOARD, null, null).index).toBe(-1);
    expect(openIndex(BOARD, null, "gone").index).toBe(-1);
  });

  it("shows each result file's verdict as the study wrote it, and marks one the register lacks", () => {
    const r = row({
      results: [result({ name: "d.json", window: "design" }), result({ name: "h.json", window: "holdout", summary: "pass", verdicts: [{ arm: null, verdict: "pass" }] })],
    });
    const out = html(<Verdicts row={r} />);
    expect(out).toContain('class="chip chip-missing verdict-fail"');
    expect(out).toContain(">design fail<");
    expect(out).toMatch(/class="chip chip-ok verdict-pass"[^>]*>holdout pass</);
    expect(out).not.toContain("not yet recorded");
    expect(html(<Verdicts row={row({ results: [result({ window: "holdout", summary: "pass", source: "queue" })] })} />)).toContain("not yet recorded");
  });

  it("labels a free-text window by its years, and a lone results.json by its verdict alone", () => {
    expect(chipLabel(result({ window: "2018-02-19..2023-12-29; holdout 2024-01-02..2026-08-21" }), false)).toBe("2018–2026");
    expect(chipLabel(result({ span: ["2022-05-16", "2024-12-31"] }), true)).toBe("2022–2024");
    expect(verdictText(result({}), false)).toBe("fail");
    expect(verdictText(result({ summary: "mixed" }), true)).toBe("results mixed");
    expect(verdictText(result({ present: false }), false)).toBe("missing");
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
