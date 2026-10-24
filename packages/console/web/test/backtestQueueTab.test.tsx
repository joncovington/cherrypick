import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ResearchQueue, ResearchTask } from "@console/shared";
import { QueueTable, argsLine, byStudy, resultLine, stamp } from "../src/pages/Backtest/QueueTab";

/**
 * The Backtest page's queue tab: the orchestrator's rows as given, grouped by study. Why a row waits
 * is the orchestrator's reason, shown verbatim; the page decides nothing.
 */

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
    createdAt: "2026-10-24T20:00:00",
    startedAt: null,
    finishedAt: null,
    runs: 0,
    exitCode: null,
    note: null,
    waits: "the ThetaData session is held by another process",
    ...over,
  };
  return { ...t, name: `${t.study}/${t.step}` };
}

function render(tasks: ResearchTask[]): string {
  const q: ResearchQueue = { sessionHeld: true, blackout: ["18:30", "20:30"], tasks };
  return renderToString(<QueueTable q={q} />).replace(/<!-- -->/g, "");
}

describe("the research queue tab", () => {
  it("shows a queued row's wait reason as the orchestrator gives it, and a ready one as ready", () => {
    const html = render([task({ id: 2 }), task({ id: 1, step: "plan", waits: "ready" })]);
    expect(html).toContain("the ThetaData session is held by another process");
    expect(html).toContain("ready: starts at the next tick");
    expect(html).toContain(">ThetaData<");
  });

  it("groups rows under their study, studies in name order, newest first within one", () => {
    const tasks = [
      task({ id: 1, study: "item17", step: "plan" }),
      task({ id: 3, study: "flow-tags", step: "fetch-flow" }),
      task({ id: 2, study: "item17", step: "fetch-chains" }),
    ];
    expect(byStudy(tasks).map(([s, ts]) => [s, ts.map((t) => t.id)])).toEqual([
      ["flow-tags", [3]],
      ["item17", [2, 1]],
    ]);
    const html = render(tasks);
    expect(html.indexOf("flow-tags")).toBeLessThan(html.indexOf("item17"));
    expect(html).toContain("item17 <span class=\"muted\">· 2 tasks</span>");
  });

  it("points a finished fetch at its counts, its input and its log, each by name", () => {
    const done = task({
      status: "done",
      waits: null,
      result: { log: "C:\\logs\\1.log", report: { complete: 40, refused: 1, stopped: false }, input: "C:\\r\\requests.json", store: "x", output: null, outputExists: null },
    });
    expect(resultLine(done)).toBe("40 complete, 1 refused · from requests.json · 1.log");
    expect(render([done])).not.toContain("Users");
  });

  it("points a script at its declared conclusion, marked when it is missing", () => {
    const pending = task({ kind: "script", step: "study", output: "C:\\out\\item17-report.md", needs: "" });
    expect(resultLine(pending)).toBe("will write item17-report.md");
    const missing = task({
      kind: "script",
      step: "study",
      status: "failed",
      output: "C:\\out\\item17-report.md",
      result: { log: "C:\\logs\\4.log", report: null, input: null, store: null, output: "C:\\out\\item17-report.md", outputExists: false },
    });
    expect(resultLine(missing)).toBe("item17-report.md (missing) · 4.log");
    expect(render([missing])).toMatch(/class="pnl-neg"[^>]*>item17-report\.md \(missing\)/);
  });

  it("shows a file argument by its name, never its full path", () => {
    expect(argsLine({ requests: "C:\\a\\b\\requests.json", workers: 3 })).toBe("requests requests.json · workers 3");
    expect(argsLine({ path: "scripts/study.py", args: ["--item", "17"] })).toBe("path study.py · args --item 17");
  });

  it("marks a failure's exit code, and says when the queue is empty", () => {
    const html = render([task({ id: 3, status: "failed", exitCode: 1, note: "refused: unknown kind", waits: null })]);
    expect(html).toMatch(/class="num pnl-neg"[^>]*>1</);
    expect(html).toContain("refused: unknown kind");
    expect(render([])).toContain("The queue is empty.");
  });

  it("formats ET stamps without a timezone shift", () => {
    expect(stamp("2026-10-26T18:30:05")).toBe("Oct 26 18:30");
    expect(stamp(null)).toBe("—");
  });
});
