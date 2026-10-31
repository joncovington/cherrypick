import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ResearchProgress, ResearchQueue, ResearchTask } from "@console/shared";
import { QueueTable, TaskDetail, ago, argsLine, etMs, headline, keep, plan, resultLine, stamp } from "../src/pages/Backtest/QueueTab";

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
    const p = plan(queue(tasks), NOW);
    expect(p.running.map((t) => t.step)).toEqual(["running"]);
    expect(p.queued.map((t) => t.step)).toEqual(["ready-early", "ready-late", "early-wait", "late-wait"]);
    expect(headline(p)).toBe("1 running · 4 queued · next: item17/ready-early");
  });

  it("keeps the last 30 days as recent, newest first, and older rows in a collapsed history by study", () => {
    const tasks = [
      task({ id: 1, study: "item17", step: "old", status: "done", finishedAt: "2026-09-01T10:00:00", waits: null }),
      task({ id: 2, study: "flow", step: "newer", status: "done", finishedAt: "2026-10-26T10:00:00", waits: null }),
      task({ id: 3, study: "item18", step: "newest", status: "failed", finishedAt: "2026-10-27T09:00:00", waits: null, exitCode: 1 }),
    ];
    const p = plan(queue(tasks), NOW);
    expect(p.recent.map((t) => t.step)).toEqual(["newest", "newer"]);
    expect(p.history.map(([s, ts]) => [s, ts.map((t) => t.step)])).toEqual([["item17", ["old"]]]);
    const html = render(queue(tasks));
    expect(html).toContain("History");
    expect(html).toContain("1 older than 30 days");
    expect(html).not.toContain("item17/old"); // collapsed until opened
    expect(html.indexOf("item18/newest")).toBeLessThan(html.indexOf("flow/newer"));
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
    expect(html).not.toContain("Recent (");
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

  it("formats ET stamps without a timezone shift", () => {
    expect(stamp("2026-10-26T18:30:05")).toBe("Oct 26 18:30");
    expect(stamp(null)).toBe("—");
  });
});
