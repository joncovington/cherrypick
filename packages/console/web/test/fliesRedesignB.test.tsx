import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { JOURNAL_SORTS, JournalBody, journalRows, type JournalRow } from "../src/pages/Flies/JournalCard";
import { PerformanceTab, completionHelp } from "../src/pages/Flies/PerformanceTab";
import { resolveBox, boxValue, type FliesBox } from "../src/pages/Flies/FliesLayout";
import { sortRows } from "../src/components/FilterSort";

/**
 * Flies decisions and completion in the portfolio tab's layout (2026-11-07): sections by question, the
 * gate glossary and the method behind the ?, the journal's runs narrowed by arm and decision, and the
 * completion detail as the tabs of one lightbox. The lightbox itself portals to the body, so the tests
 * read its pieces (`resolveBox`, the bodies) and the page that opens it.
 */

const html = (el: ReactElement, client = new QueryClient()) =>
  renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

const run = (over: Partial<JournalRow>): JournalRow => ({
  arm: "control",
  mode: "credit_first",
  reason: "duplicate_structure",
  accepted: false,
  firstSeen: "2026-11-06T10:00:00",
  lastSeen: "2026-11-06T10:30:00",
  occurrences: 5,
  centerLast: 5000,
  detail: null,
  ...over,
});

const rows: JournalRow[] = [
  run({}),
  run({ arm: "gate-a", reason: "entered", accepted: true, firstSeen: "2026-11-06T09:45:00", occurrences: 1 }),
  run({ arm: "gate-a", reason: "ladder", firstSeen: "2026-11-06T11:00:00", occurrences: 20 }),
];

describe("the decisions tab", () => {
  it("sits under its question, with the glossary behind the ? and not in the page", () => {
    const out = html(<JournalBody rows={rows} date="2026-11-06" loading={false} />);
    expect(out).toContain("What did each arm decide?");
    expect(out).toContain('aria-label="About decision journal · 2026-11-06"');
    // The gloss for a gate is opened from the ?, never drawn into the card.
    expect(out).not.toContain("already open today");
    expect(out).not.toContain("hover");
    expect(out).toContain("every decision");
    expect(out).toContain("3 of 3 runs");
  });

  it("narrows by arm and by decision, and says how many runs are left", () => {
    expect(journalRows(rows, "gate-a", "all")).toHaveLength(2);
    expect(journalRows(rows, null, "taken").map((r) => r.reason)).toEqual(["entered"]);
    expect(journalRows(rows, "gate-a", "refused").map((r) => r.reason)).toEqual(["ladder"]);
  });

  it("orders on timing, size and the arm's name", () => {
    expect(JOURNAL_SORTS.map((s) => s.id)).toEqual(["time", "length", "arm"]);
    const by = (id: string) => sortRows(rows, JOURNAL_SORTS.find((s) => s.id === id), false).map((r) => r.reason);
    expect(by("time")).toEqual(["entered", "duplicate_structure", "ladder"]);
    expect(by("length")).toEqual(["ladder", "duplicate_structure", "entered"]);
    expect(by("arm")[0]).toBe("duplicate_structure");
  });

  it("says there were no decisions rather than drawing an empty chart", () => {
    const out = html(<JournalBody rows={[]} date="2026-11-06" loading={false} />);
    expect(out).toContain("no decisions recorded for this day");
    expect(out).not.toContain("<svg");
  });

  it("leaves the module-wide filter's slot where the tab's top is", () => {
    const out = html(<JournalBody rows={rows} date={null} loading={false} filterSlot={<div id="slot-mark">the filter</div>} />);
    expect(out.indexOf("slot-mark")).toBeGreaterThan(-1);
    expect(out.indexOf("slot-mark")).toBeLessThan(out.indexOf("What did each arm decide?"));
  });
});

const perf = {
  tiles: { trades: 20, netPnl: 120, winRatePct: 55, profitFactor: 1.2, feeDragPct: 40, completionRatePct: 70 },
  series: [],
  equity: [],
  risk: { sharpe: null, sortino: null, calmar: null, recoveryFactor: null, sampleSize: 0, undersampledFlag: false, sharpeOverfitFlag: false },
  roll: null,
  completion: {
    leggedEntries: 20,
    completed: 14,
    completionRatePct: 70,
    neverOffered: 3,
    bufferBlocked: 2,
    floorBlocked: 1,
    unknown: 0,
    medianLatencyMin: 16,
    minLatencyMin: 2,
    maxLatencyMin: 60,
    medianSpotMove: 1.5,
  },
  completionTrend: [],
  liveVsPaper: null,
  leftOnTable: null,
};

function completion(over: Record<string, unknown> = {}): string {
  const client = new QueryClient();
  client.setQueryData(["flies-performance", "paper", "daily", null, null, null], { ...perf, ...over });
  return html(<PerformanceTab mode="paper" filter={{ arm: null, date: null, symbol: null, era: null }} />, client);
}

describe("the completion tab", () => {
  it("leads with its tiles, and the 1-lot caveat is a flag on the equity card", () => {
    const out = completion();
    expect(out).toContain("How has it done?");
    expect(out).toContain("1-lot samples");
    expect(out).toContain("Is completion good enough to be real?");
    expect(out).toContain("median minutes");
  });

  it("opens why misses missed and the by-session trend from the card, and keeps the tables off the page", () => {
    const out = completion();
    expect(out).toContain("why misses missed");
    expect(out).toContain("by session");
    expect(out).not.toContain("blocked by fee_buffer");
    expect(out).not.toContain("live vs paper");
    expect(out).not.toContain("left on table");
  });

  it("offers live vs paper, roll and left on table only when the scope has them, with the halt rule flagged", () => {
    const lvp = {
      arm: "control",
      live: { entries: 12, completed: 8, completionRatePct: 66, medianLatencyMin: 10, avgCredit: 1 },
      paper: { entries: 12, completed: 9, completionRatePct: 75, medianLatencyMin: 9, avgCredit: 1 },
      completionGapPct: 9,
      abort: { gapLimitPct: 20, minLiveEntries: 10, armed: true, triggered: false },
      degraded: null,
    };
    const roll = { ...perf.completion, leggedEntries: 8 };
    const lot = { entryMode: "debit_first", untracked: 0, overall: { n: 4, improved: 2, medianImprovementPts: 0.1, maxImprovementPts: 0.2, medianImprovementDollars: 5, totalImprovementDollars: 20 }, byGexBucket: {} };
    const out = completion({ liveVsPaper: lvp, roll, leftOnTable: lot });
    expect(out).toContain("live vs paper");
    expect(out).toContain("roll <span");
    expect(out).toContain("left on table");
    expect(out).toContain("halt rule");
    expect(out).toContain("abort rule armed");
    const warn = renderToString(<>{completionHelp(lvp as never).body}</>);
    expect(warn).toContain("help-warn");
    expect(warn).toContain("Halts the pilot if the completion gap exceeds 20pp with at least 10 live entries.");
    expect(renderToString(<>{completionHelp(null).body}</>)).not.toContain("help-warn");
  });
});

describe("a card's lightbox value", () => {
  const box = (id: string, tabs: string[]): FliesBox => ({ id, kicker: "k", title: id, tabs: tabs.map((t) => ({ id: t, label: t })), body: () => null });
  const boxes = [box("a", ["x", "y"]), box("a-long", ["z"]), box("b", ["x"])];

  it("names a box, and a tab of it", () => {
    expect(resolveBox("a", boxes)).toMatchObject({ index: 0, tab: "x" });
    expect(resolveBox(boxValue("a", "y"), boxes)).toMatchObject({ index: 0, tab: "y" });
  });

  it("falls back to the first tab for an unknown tab and to nothing for an unknown box", () => {
    expect(resolveBox("a:nope", boxes)).toMatchObject({ index: 0, tab: "x" });
    expect(resolveBox("c", boxes)).toBeNull();
    expect(resolveBox(null, boxes)).toBeNull();
  });

  it("does not take a longer id for the box whose name it starts with", () => {
    expect(resolveBox("a-long", boxes)).toMatchObject({ index: 1, tab: "z" });
  });
});
