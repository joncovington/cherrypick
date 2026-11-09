import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ResearchProgress, ResearchQueue, ResearchTask, StudyRow } from "@console/shared";
import {
  QueueCards,
  QueueDialogBody,
  TaskDetail,
  ago,
  argRows,
  argsLine,
  etMs,
  etaText,
  BacktestQueueTab,
  headline,
  paceText,
  rateText,
  plan,
  queueItems,
  queuePollMs,
  resultLine,
  stamp,
} from "../src/pages/Backtest/QueueTab";
import { indexStudies, splitTitle, type StudyIndex } from "../src/pages/Backtest/studyIndex";
import { ListViewContext } from "../src/components/ViewSwitch";

describe("the research queue tab: rows", () => {
  it("lists up next and the failures as rows, in the same order, with the running task still large", () => {
    const tasks = [
      task({ id: 6, step: "running", status: "running", startedAt: "2026-10-27T11:00:00", waits: null }),
      task({ id: 3, step: "ready-early", waits: "ready" }),
      task({ id: 2, step: "early-wait" }),
      task({ id: 4, step: "study-failed", status: "failed", finishedAt: "2026-10-27T09:00:00", waits: null, exitCode: 1 }),
    ];
    const out = wrap(
      <ListViewContext.Provider value="rows">
        <QueueCards q={queue(tasks)} nowMs={NOW} />
      </ListViewContext.Provider>,
    );
    expect(out).toContain('class="study-tile study-hero q-hero" data-task="6"');
    expect(out.match(/class="bt-row"/g)?.length).toBe(3);
    expect(out).not.toContain('class="study-tile" data-task');
    expect(out.indexOf("ready-early")).toBeLessThan(out.indexOf("early-wait"));
    expect(out).toContain('class="tr-item bt-item is-attn"');
    expect(out).toContain("ready: starts next");
  });
});

/**
 * The Backtest page's queue tab (as cards 2026-11-03): the running task large, then up next in the
 * runner's order, the last week's failures and runs started by hand, each opening to a lightbox. Why
 * a row waits is the orchestrator's reason, shown verbatim; the page decides nothing about the
 * queue, only how to lay it out.
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
    rate: { phase: "sessions", secondsEach: 57.2 },
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

const wrap = (node: React.ReactNode) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

function render(q: ResearchQueue, studies?: StudyIndex): string {
  return wrap(<QueueCards q={q} nowMs={NOW} studies={studies} />);
}

describe("the research queue tab: the footer", () => {
  it("keeps the wait rules and queue-add syntax behind the card's ?, not on the page", () => {
    const out = wrap(<BacktestQueueTab />).replace(/<!-- -->/g, "");
    expect(out).toContain("About Research queue");
    expect(out).not.toContain("queue-add");
    expect(out).not.toContain("--not-before");
  });
});

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
    // The running task is the large tile; up next is numbered in the runner's order.
    const items = queueItems(queue(tasks), NOW);
    expect(items.map((i) => (i.kind === "task" ? `${i.section}:${i.t.step}` : i.key))).toEqual([
      "running:running",
      "next:ready-early",
      "next:ready-late",
      "next:early-wait",
      "next:late-wait",
    ]);
    const html = render(queue(tasks));
    expect(html).toContain('class="study-tile study-hero q-hero" data-task="6"');
    expect(html.indexOf("ready-early")).toBeLessThan(html.indexOf("ready-late"));
    expect(html.indexOf("ready-late")).toBeLessThan(html.indexOf("early-wait"));
    expect(html).toContain('class="q-pos">1<');
    expect(html).toContain('class="q-pos is-waiting">3<');
    expect(html.match(/>ready</g)).toHaveLength(2);
    expect(html.match(/>waiting</g)).toHaveLength(2);
  });

  it("keeps finished rows off the tab, except a failure or stop in the last week, newest first", () => {
    const tasks = [
      task({ id: 1, step: "fetch", status: "done", finishedAt: "2026-10-26T10:00:00", waits: null }),
      task({ id: 2, step: "study-old", status: "failed", finishedAt: "2026-10-01T10:00:00", waits: null, exitCode: 1 }),
      task({ id: 3, step: "study-stopped", status: "stopped", finishedAt: "2026-10-25T10:00:00", waits: null }),
      task({ id: 4, step: "study-failed", status: "failed", finishedAt: "2026-10-27T09:00:00", waits: null, exitCode: 1 }),
      task({ id: 5, step: "plan", status: "cancelled", finishedAt: "2026-10-27T09:30:00", waits: null }),
    ];
    const p = plan(queue(tasks), undefined, NOW);
    expect(p.attention.map((t) => t.step)).toEqual(["study-failed", "study-stopped"]);
    const html = render(queue(tasks));
    expect(html).toContain("Failed or stopped");
    expect(html).toContain("last 7 days");
    expect(html.indexOf("study-failed")).toBeLessThan(html.indexOf("study-stopped"));
    for (const gone of ["· fetch ·", "study-old", "· plan ·"]) expect(html).not.toContain(gone);
    expect(html).toContain("Nothing running");
  });

  it("shows a queued row's wait reason as the orchestrator gives it, and a ready one as ready", () => {
    const html = render(queue([task({ id: 2 }), task({ id: 1, step: "plan", waits: "ready" })]));
    expect(html).toContain("the ThetaData session is held by another process");
    // The first ready row starts next; a ready row behind it says it is in line.
    expect(html).toContain("ready: starts next");
    const two = render(queue([task({ id: 1, step: "a", waits: "ready" }), task({ id: 2, step: "b", waits: "ready" })]));
    expect(two).toContain("ready: starts next");
    expect(two).toContain("ready, in line behind the rows above");
    expect(html).toContain("· ThetaData");
  });

  it("shows a run started by hand under Now with its meter, and a finished one under Recent", () => {
    const html = render(queue([], [byHand({}), byHand({ file: "x-1", job: "meic-replay-check", state: "done", fraction: 1, line: "sessions 4/4 days" })]));
    expect(html).toContain("backtest-run-meic");
    expect(html).toContain("Started by hand");
    expect(html).toContain("30.0% · sessions 3/10 days");
    expect(html.indexOf("backtest-run-meic")).toBeLessThan(html.indexOf("meic-replay-check"));
  });

  it("says when the queue is empty", () => {
    expect(render(queue([]))).toContain("The queue is empty.");
    expect(render(queue([], [byHand({})]))).not.toContain("The queue is empty.");
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
    // The collapsed row shows names, never paths (a failed row is the finished one this tab shows).
    const html = render(queue([{ ...done, status: "failed" }]));
    expect(html).toContain("1.log");
    expect(html).not.toContain("Users");
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
    expect(render(queue([missing]))).toMatch(/pnl-neg[^>]*>item17-report\.md \(missing\)/);
    // The lightbox's result tab names the output, marked missing, and the log by name.
    const item = queueItems(queue([missing]), NOW)[0]!;
    const box = wrap(<QueueDialogBody item={item} tab="result" onTab={() => undefined} nowMs={NOW} position="1 of 1" onPrev={() => undefined} onNext={() => undefined} onClose={() => undefined} />);
    expect(box).toContain("item17-report.md (missing)");
    expect(box).toContain("4.log");
    expect(box).not.toContain("C:\\out");
  });
});

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

describe("the research queue tab: naming a study", () => {
  const done = (over: Partial<ResearchTask>) => task({ status: "queued", waits: "ready", needs: "", ...over });

  it("leads with the register title, split at its colon, and keeps the slug as a tag", () => {
    expect(splitTitle("The wing width of MEIC's live rule: 15, 20, 25 and 30 against 10")).toEqual([
      "The wing width of MEIC's live rule",
      "15, 20, 25 and 30 against 10",
    ]);
    expect(splitTitle("No colon here")).toEqual(["No colon here", null]);
    const studies = board([{ study: "item43", title: "The wing width of MEIC's live rule: 15, 20, 25 and 30 against 10" }]);
    const html = render(queue([done({ study: "item43", step: "study-design" })]), studies);
    expect(html).toContain("The wing width of MEIC&#x27;s live rule");
    expect(html).not.toContain("15, 20, 25 and 30 against 10");
    expect(html).toContain('class="study-tile-slug">item43<');
    // The lightbox names it in full, the slug as a tag.
    const item = queueItems(queue([done({ study: "item43", step: "study-design" })]), NOW)[0]!;
    const box = wrap(<QueueDialogBody item={item} tab="overview" onTab={() => undefined} studies={studies} nowMs={NOW} position="1 of 1" onPrev={() => undefined} onNext={() => undefined} onClose={() => undefined} />);
    expect(box).toContain("<strong>The wing width of MEIC&#x27;s live rule</strong>");
    expect(box).toContain('class="chip muted queue-slug">item43<');
  });

  it("names a study the register lacks by its slug, and says so", () => {
    const html = render(queue([done({ study: "gate-oracle", step: "pm-200-design" })]), board([]));
    expect(html).toContain("gate-oracle");
    expect(html).toContain("not in the register");
    // Before the board loads there is no index, and nothing is said about the register.
    expect(render(queue([done({ study: "gate-oracle", step: "pm-200-design" })]))).not.toContain("not in the register");
  });
});

describe("the research queue tab: helpers", () => {
  it("says how long ago, from ET stamps", () => {
    expect(ago(etMs("2026-10-27T11:48:00"), NOW)).toBe("12 min ago");
    expect(ago(etMs("2026-10-27T09:00:00"), NOW)).toBe("3 h ago");
    expect(ago(etMs("2026-10-20T12:00:00"), NOW)).toBe("7 d ago");
    expect(ago(null, NOW)).toBe("—");
  });

  it("words a meter's time left, and lists each argument with a file by its name", () => {
    expect(etaText(3900)).toBe("about 1 h 5 min left");
    expect(etaText(600)).toBe("about 10 min left");
    expect(etaText(20)).toBe("under a minute left");
    expect(etaText(null)).toBeNull();
    // the rate sits beside the time left (2026-11-06: the card showed only the time left)
    expect(rateText({ phase: "sessions", secondsEach: 57.24 })).toBe("57.2 s/session");
    expect(rateText({ phase: "symbol-days", secondsEach: 0.25 })).toBe("4.0 symbol-days/s");
    expect(rateText(null)).toBeNull();
    expect(paceText(byHand({ etaSeconds: 3900 }))).toBe("57.2 s/session · about 1 h 5 min left");
    expect(paceText(byHand({ rate: null, etaSeconds: null }))).toBe("");
    expect(argRows({ requests: "C:\\a\\b\\requests.json", workers: 3, args: ["--item", "C:\\x\\y.json"] })).toEqual([
      ["requests", "requests.json"],
      ["workers", "3"],
      ["args", "--item y.json"],
    ]);
  });

  it("opens a card from the URL, and steps through every card in layout order", () => {
    const q = queue([task({ id: 9, status: "running", startedAt: "2026-10-27T11:00:00", waits: null }), task({ id: 4, waits: "ready" })], [byHand({})]);
    expect(queueItems(q, NOW).map((i) => i.key)).toEqual(["9", "hand:backtest-run-meic-77", "4"]);
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
