import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ResearchProgress, ResearchQueue, ResearchTask, StudyResult, StudyRow } from "@console/shared";
import {
  QueueTable,
  TaskDetail,
  ago,
  argsLine,
  chipLabel,
  etMs,
  headline,
  keep,
  plan,
  queuePollMs,
  resultLine,
  stamp,
  verdictText,
} from "../src/pages/Backtest/QueueTab";
import { indexStudies, splitTitle, type StudyIndex } from "../src/pages/Backtest/studyIndex";

/**
 * The Backtest page's queue tab (laid out again 2026-10-27): now / recent (30 days) / history, five
 * columns, rows that open to the rest. Why a row waits is the orchestrator's reason, shown verbatim;
 * the page decides nothing about the queue, only how to lay it out.
 */

const NOW = etMs("2026-10-27T12:00:00") as number;

function task(over: Partial<ResearchTask>): ResearchTask {
  const t: ResearchTask = {
    id: 1,
    study: "item17",
    step: "fetch-chains",
    name: "item17/fetch-chains",
    kind: "fetch-chains",
    output: null,
    result: null,
    args: { requests: "C:\\Users\\x\\.cherrypick\\research\\item17\\requests.json", workers: 3 },
    needs: "thetadata",
    notBefore: null,
    stopBy: null,
    status: "queued",
    createdAt: "2026-10-26T20:00:00",
    startedAt: null,
    finishedAt: null,
    runs: 0,
    exitCode: null,
    note: null,
    waits: "the ThetaData session is held by another process",
    progress: null,
    ...over,
  };
  return { ...t, name: `${t.study}/${t.step}` };
}

function byHand(over: Partial<ResearchProgress>): ResearchProgress {
  return {
    file: "backtest-run-meic-77",
    job: "backtest-run-meic",
    state: "running",
    phases: [{ name: "sessions", done: 3, total: 10, unit: "days", current: "2026-10-14" }],
    fraction: 0.3,
    etaSeconds: 600,
    ageSeconds: 2,
    line: "sessions 3/10 days (2026-10-14) · ~00:10 remaining",
    note: null,
    startedAt: Date.now() / 1000 - 300,
    updatedAt: Date.now() / 1000 - 2,
    ...over,
  };
}

function queue(tasks: ResearchTask[], hand: ResearchProgress[] = []): ResearchQueue {
  return { sessionHeld: true, blackout: ["18:30", "20:30"], tasks, byHand: hand };
}

function render(q: ResearchQueue, opts: { status?: "all" | "active" | "failed" | "done"; study?: string } = {}): string {
  return renderToString(<QueueTable q={q} nowMs={NOW} {...opts} />).replace(/<!-- -->/g, "");
}

describe("the research queue tab: sections", () => {
  it("puts running first, then queued in the runner's order: ready first, oldest first", () => {
    const tasks = [
      task({ id: 5, step: "late-wait" }),
      task({ id: 4, step: "ready-late", waits: "ready" }),
      task({ id: 2, step: "early-wait" }),
      task({ id: 3, step: "ready-early", waits: "ready" }),
      task({ id: 6, step: "running", status: "running", startedAt: "2026-10-27T11:00:00", waits: null }),
    ];
    const p = plan(queue(tasks));
    expect(p.running.map((t) => t.step)).toEqual(["running"]);
    expect(p.queued.map((t) => t.step)).toEqual(["ready-early", "ready-late", "early-wait", "late-wait"]);
    expect(headline(p)).toBe("1 running · 4 queued · next: item17/ready-early");
    // Under Now: ready ones, then waiting ones, each under its own head.
    const html = render(queue(tasks));
    expect(html).toContain("ready · 2 · oldest first: the top one starts next");
    expect(html).toContain("waiting · 2");
    expect(html.indexOf("ready-late")).toBeLessThan(html.indexOf("waiting · 2"));
    expect(html.indexOf("waiting · 2")).toBeLessThan(html.indexOf("early-wait"));
  });

  it("groups finished rows by study, the most recently finished study first, its steps newest first", () => {
    const tasks = [
      task({ id: 1, study: "item17", step: "fetch", status: "done", finishedAt: "2026-09-01T10:00:00", waits: null }),
      task({ id: 2, study: "item17", step: "study", status: "done", finishedAt: "2026-09-02T10:00:00", waits: null }),
      task({ id: 3, study: "item18", step: "study", status: "failed", finishedAt: "2026-10-27T09:00:00", waits: null, exitCode: 1 }),
    ];
    const p = plan(queue(tasks));
    expect(p.studies.map((l) => [l.study, l.tasks.map((t) => t.step)])).toEqual([
      ["item18", ["study"]],
      ["item17", ["study", "fetch"]],
    ]);
    const html = render(queue(tasks));
    expect(html).toContain("Studies");
    expect(html).toContain("2 steps: study done · fetch done");
    expect(html.indexOf("item18")).toBeLessThan(html.indexOf("item17"));
  });

  it("leaves cancelled rows out unless asked, and counts a study that has nothing else", () => {
    const tasks = [
      task({ id: 1, study: "item17", step: "study", status: "done", finishedAt: "2026-10-20T10:00:00", waits: null }),
      task({ id: 2, study: "item17", step: "study", status: "cancelled", finishedAt: "2026-10-21T10:00:00", waits: null }),
      task({ id: 3, study: "item44", step: "plan", status: "cancelled", finishedAt: "2026-10-22T10:00:00", waits: null }),
    ];
    const p = plan(queue(tasks));
    expect(p.studies.map((l) => [l.study, l.tasks.length, l.cancelled])).toEqual([["item17", 1, 1]]);
    expect(p.cancelledOnly).toBe(1);
    expect(render(queue(tasks))).toContain("1 with only cancelled rows not shown");
    const all = plan(queue(tasks), undefined, true);
    expect(all.studies.map((l) => l.study)).toEqual(["item44", "item17"]);
    expect(render(queue([tasks[2]]))).toContain("Only cancelled rows match");
  });

  it("shows a queued row's wait reason as the orchestrator gives it, and a ready one as ready", () => {
    const html = render(queue([task({ id: 2 }), task({ id: 1, step: "plan", waits: "ready" })]));
    expect(html).toContain("the ThetaData session is held by another process");
    expect(html).toContain("ready: starts at the next tick");
    expect(html).toContain(">ThetaData<");
  });

  it("shows a run started by hand under Now with its meter, and a finished one under Recent", () => {
    const html = render(queue([], [byHand({}), byHand({ file: "x-1", job: "meic-replay-check", state: "done", fraction: 1, line: "sessions 4/4 days" })]));
    expect(html).toContain("backtest-run-meic");
    expect(html).toContain(">by hand<");
    expect(html).toContain("30.0% · sessions 3/10 days");
    expect(html.indexOf("backtest-run-meic")).toBeLessThan(html.indexOf("meic-replay-check"));
  });

  it("keeps a finished run started by hand, and the Recent section, out of running + queued", () => {
    const q = queue([task({})], [byHand({}), byHand({ file: "x-1", job: "meic-replay-check", state: "done", fraction: 1, line: "sessions 4/4 days" })]);
    const html = render(q, { status: "active" });
    expect(html).toContain("backtest-run-meic");
    expect(html).not.toContain("meic-replay-check");
    expect(html).not.toContain(">Studies <");
  });

  it("says when the queue is empty, and when the filter hides everything", () => {
    expect(render(queue([]))).toContain("The queue is empty.");
    expect(render(queue([task({})]), { status: "failed" })).toContain("Nothing matches the filter.");
  });
});

describe("the research queue tab: rows", () => {
  it("opens to the fields that do not fit: arguments, timings, exit code, result fields", () => {
    const failed = task({
      status: "failed",
      exitCode: 1,
      runs: 2,
      startedAt: "2026-10-27T09:00:00",
      finishedAt: "2026-10-27T09:30:00",
      result: { log: "C:\\logs\\3.log", report: { complete: 40, refused: 1 }, input: "C:\\r\\requests.json", store: null, output: null, outputExists: null },
    });
    const html = renderToString(<TaskDetail t={failed} withLog={false} />).replace(/<!-- -->/g, "");
    expect(html).toContain('<span class="pnl-neg">1</span>');
    expect(html).toContain("C:\\r\\requests.json");
    expect(html).toContain("<dt>refused</dt><dd>1</dd>");
    expect(html).toContain("Oct 27 09:00 · Oct 27 09:30");
  });

  it("points a finished fetch at its counts, its input and its log, each by name, in the row", () => {
    const done = task({
      status: "done",
      waits: null,
      finishedAt: "2026-10-27T10:00:00",
      result: { log: "C:\\logs\\1.log", report: { complete: 40, refused: 1, stopped: false }, input: "C:\\r\\requests.json", store: "x", output: null, outputExists: null },
    });
    expect(resultLine(done)).toBe("40 complete, 1 refused · from requests.json · 1.log");
    expect(render(queue([done]))).not.toContain("Users"); // the collapsed row shows names, never paths
  });

  it("points a script at its declared conclusion, marked when it is missing", () => {
    const pending = task({ kind: "script", step: "study", output: "C:\\out\\item17-report.md", needs: "" });
    expect(resultLine(pending)).toBe("will write item17-report.md");
    const missing = task({
      kind: "script",
      step: "study",
      status: "failed",
      finishedAt: "2026-10-27T10:00:00",
      output: "C:\\out\\item17-report.md",
      result: { log: "C:\\logs\\4.log", report: null, input: null, store: null, output: "C:\\out\\item17-report.md", outputExists: false },
    });
    expect(resultLine(missing)).toBe("item17-report.md (missing) · 4.log");
    expect(render(queue([missing]))).toMatch(/class="pnl-neg"[^>]*>item17-report\.md \(missing\)/);
  });
});

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

function board(rows: Array<Partial<StudyRow> & { study: string; title: string }>): StudyIndex {
  return indexStudies({
    rows: rows.map((r, i) => ({
      id: i + 1,
      status: "failed",
      legacy: false,
      pointer: null,
      outcome: null,
      measure: null,
      expected: null,
      sample: null,
      rule: null,
      mechanism: null,
      rivals: [],
      group: "finished",
      results: [],
      queueActive: false,
      tasks: [],
      lastActivity: null,
      ...r,
    })),
    unregistered: [],
  });
}

describe("the research queue tab: naming a study and its results", () => {
  const done = (over: Partial<ResearchTask>) => task({ status: "done", finishedAt: "2026-10-27T10:00:00", waits: null, needs: "", ...over });

  it("leads with the register title, split at its colon, and keeps the slug as a tag", () => {
    expect(splitTitle("The wing width of MEIC's live rule: 15, 20, 25 and 30 against 10")).toEqual([
      "The wing width of MEIC's live rule",
      "15, 20, 25 and 30 against 10",
    ]);
    expect(splitTitle("No colon here")).toEqual(["No colon here", null]);
    const studies = board([{ study: "item43", title: "The wing width of MEIC's live rule: 15, 20, 25 and 30 against 10" }]);
    const html = renderToString(<QueueTable q={queue([done({ study: "item43", step: "study-design" })])} nowMs={NOW} studies={studies} />).replace(/<!-- -->/g, "");
    expect(html).toContain("<strong>The wing width of MEIC&#x27;s live rule</strong>");
    expect(html).toContain(": 15, 20, 25 and 30 against 10");
    expect(html).toContain('class="chip muted queue-slug">item43<');
  });

  it("shows each result file's verdict and the decision, as the board read them", () => {
    const studies = board([
      {
        study: "item24",
        title: "The entry slot of MEIC's live rule: 12:00 to 13:30 against 14:00",
        outcome: "live stays at 14:00; the afternoon slot is closed as a lever",
        results: [result({ name: "d.json", window: "design" }), result({ name: "h.json", window: "holdout", summary: "pass", verdicts: [{ arm: null, verdict: "pass" }] })],
      },
    ]);
    const html = renderToString(<QueueTable q={queue([done({ study: "item24", step: "holdout" })])} nowMs={NOW} studies={studies} />).replace(/<!-- -->/g, "");
    expect(html).toContain('class="chip chip-missing"');
    expect(html).toContain(">design fail<");
    expect(html).toMatch(/class="chip chip-ok"[^>]*>holdout pass</);
    expect(html).toContain("live stays at 14:00; the afternoon slot is closed as a lever");
  });

  it("labels a free-text window by its years, and a lone results.json by its verdict alone", () => {
    expect(chipLabel(result({ window: "2018-02-19..2023-12-29; holdout 2024-01-02..2026-08-21" }), false)).toBe("2018–2026");
    expect(chipLabel(result({ span: ["2022-05-16", "2024-12-31"] }), true)).toBe("2022–2024");
    expect(verdictText(result({}), false)).toBe("fail");
    expect(verdictText(result({ summary: "mixed" }), true)).toBe("results mixed");
    expect(verdictText(result({ present: false }), false)).toBe("missing");
  });

  it("says when a verdict came from the queue and the register does not record it yet", () => {
    const row = { study: "item41", title: "The stabilization entry gate at real quotes: does waiting help?" };
    const recorded = board([{ ...row, results: [result({ window: "design" })] }]);
    const q = queue([done({ study: "item41", step: "study-holdout" })]);
    const strip = (s: string) => s.replace(/<!-- -->/g, "");
    expect(strip(renderToString(<QueueTable q={q} nowMs={NOW} studies={recorded} />))).not.toContain("not yet recorded");
    const unrecorded = board([{ ...row, status: "declared", results: [result({ window: "holdout", summary: "pass", source: "queue" })] }]);
    const html = strip(renderToString(<QueueTable q={q} nowMs={NOW} studies={unrecorded} />));
    expect(html).toContain(">holdout pass<");
    expect(html).toContain("not yet recorded");
  });

  it("names a study the register lacks by its slug, and says so", () => {
    const html = renderToString(<QueueTable q={queue([done({ study: "gate-oracle", step: "pm-200-design" })])} nowMs={NOW} studies={board([])} />).replace(/<!-- -->/g, "");
    expect(html).toContain("<strong>gate-oracle</strong>");
    expect(html).toContain("not in the register");
    // Before the board loads there is no index, and nothing is said about the register.
    expect(render(queue([done({ study: "gate-oracle", step: "pm-200-design" })]))).not.toContain("not in the register");
  });
});

describe("the research queue tab: helpers", () => {
  it("filters by status and study", () => {
    expect(keep(task({ status: "running" }), "active", "")).toBe(true);
    expect(keep(task({ status: "done" }), "active", "")).toBe(false);
    expect(keep(task({ status: "failed" }), "failed", "")).toBe(true);
    expect(keep(task({ study: "flow" }), "all", "item17")).toBe(false);
  });

  it("says how long ago, from ET stamps", () => {
    expect(ago(etMs("2026-10-27T11:48:00"), NOW)).toBe("12 min ago");
    expect(ago(etMs("2026-10-27T09:00:00"), NOW)).toBe("3 h ago");
    expect(ago(etMs("2026-10-20T12:00:00"), NOW)).toBe("7 d ago");
    expect(ago(null, NOW)).toBe("—");
  });

  it("shows a file argument by its name, never its full path", () => {
    expect(argsLine({ requests: "C:\\a\\b\\requests.json", workers: 3 })).toBe("requests requests.json · workers 3");
    expect(argsLine({ path: "scripts/study.py", args: ["--item", "17"] })).toBe("path study.py · args --item 17");
  });

  it("polls every 2 s while anything runs, 5 s while rows wait, 30 s otherwise", () => {
    const ok = (q: ResearchQueue) => ({ ok: true as const, data: q, error: null });
    expect(queuePollMs(ok(queue([task({ status: "running", waits: null }), task({ id: 2 })])))).toBe(2_000);
    expect(queuePollMs(ok(queue([], [byHand({})])))).toBe(2_000);
    expect(queuePollMs(ok(queue([task({})], [byHand({ state: "done" })])))).toBe(5_000);
    expect(queuePollMs(ok(queue([task({ status: "done" })])))).toBe(30_000);
    expect(queuePollMs(undefined)).toBe(30_000);
    expect(queuePollMs({ ok: false, data: null, error: "down" })).toBe(30_000);
  });

  it("formats ET stamps without a timezone shift", () => {
    expect(stamp("2026-10-26T18:30:05")).toBe("Oct 26 18:30");
    expect(stamp(null)).toBe("—");
  });
});
