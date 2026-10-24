import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ResearchQueue, ResearchTask } from "@console/shared";
import { QueueTable, argsLine, stamp } from "../src/pages/Backtest/QueueTab";

/**
 * The Backtest page's queue tab: the orchestrator's rows as given. Why a row waits is the
 * orchestrator's reason, shown verbatim; the page decides nothing.
 */

function task(over: Partial<ResearchTask>): ResearchTask {
  return {
    id: 1,
    kind: "fetch-chains",
    label: "item 17 chains",
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
}

function render(tasks: ResearchTask[]): string {
  const q: ResearchQueue = { sessionHeld: true, blackout: ["18:30", "20:30"], tasks };
  return renderToString(<QueueTable q={q} />).replace(/<!-- -->/g, "");
}

describe("the research queue tab", () => {
  it("shows a queued row's wait reason as the orchestrator gives it, and a ready one as ready", () => {
    const html = render([task({ id: 2 }), task({ id: 1, waits: "ready", label: "next" })]);
    expect(html).toContain("the ThetaData session is held by another process");
    expect(html).toContain("ready: starts at the next tick");
    expect(html).toContain(">ThetaData<");
  });

  it("shows a file argument by its name, never its full path", () => {
    expect(argsLine({ requests: "C:\\a\\b\\requests.json", workers: 3 })).toBe("requests requests.json · workers 3");
    expect(argsLine({ path: "scripts/study.py", args: ["--item", "17"] })).toBe("path study.py · args --item 17");
    expect(render([task({})])).not.toContain("Users");
  });

  it("marks a failure's exit code, and a finished row carries its note", () => {
    const html = render([
      task({ id: 3, status: "failed", exitCode: 1, note: "refused: unknown kind", waits: null }),
      task({ id: 4, status: "done", exitCode: 0, runs: 2, finishedAt: "2026-10-26T02:10:00", waits: null }),
    ]);
    expect(html).toMatch(/class="num pnl-neg"[^>]*>1</);
    expect(html).toContain("refused: unknown kind");
    expect(html).toContain("Oct 26 02:10");
  });

  it("says when the queue is empty", () => {
    expect(render([])).toContain("The queue is empty.");
  });

  it("formats ET stamps without a timezone shift", () => {
    expect(stamp("2026-10-26T18:30:05")).toBe("Oct 26 18:30");
    expect(stamp("2026-10-26T18:30")).toBe("Oct 26 18:30");
    expect(stamp(null)).toBe("—");
  });
});
