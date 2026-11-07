import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { StudyReport, StudyReportArm } from "@console/shared";
import { ADVICE_WORD, ReportBody, measureT, measureValue, windowFill } from "../src/pages/Backtest/StudyReport";
import { mondayOf } from "../src/pages/Backtest/StudyWeek";
import { DIALOG_TABS } from "../src/pages/Backtest/StudyCards";

/**
 * A study's Report tab prints the orchestrator's report (`study-report`) and works nothing out: the
 * reading and advice are its words, the chart its figures on the measure picked. Invented figures.
 */

function arm(over: Partial<StudyReportArm>): StudyReportArm {
  return {
    arm: "wide",
    group: "arms",
    window: "design",
    file: "study-design.json",
    mean: 12,
    t: 2.1,
    n: 40,
    p: null,
    verdict: "fail",
    sessions: 100,
    entryRate: 0.4,
    netPerTrade: 30,
    netPerTradeDerived: true,
    perRisk: 0.02,
    perRiskBase: 0.03,
    paired: { mean: 2, t: 0.5, n: 100, p: 0.3 },
    pairedVs: "base",
    pairedBoth: { mean: 20, t: 3, n: 40, p: 0.001 },
    ...over,
  };
}

const REPORT: StudyReport = {
  study: "item7",
  provisional: true,
  windows: ["design", "holdout"],
  arms: [arm({}), arm({ window: "holdout", mean: -3, paired: { mean: -1, t: -0.2, n: 50, p: 0.6 }, pairedBoth: null })],
  comparisons: [],
  verdicts: [],
  measures: [
    { key: "paired", label: "Gain over baseline", note: "The arm minus its baseline.", t: "paired" },
    { key: "per_risk", label: "Return on buying power", note: "Net over buying power.", t: null },
  ],
  gates: [{ key: "stabilization", name: "Stabilization gate", text: "Waits for **two** flat bars." }],
  reading: [{ kind: "trades_less", text: "**wide trades better but trades less (design).**" }],
  advice: [{ kind: "new_study", text: "Declare a fallback study: take **wide** first.", why: "wide beats the baseline when both trade" }],
  failedSteps: [],
};

describe("the Report tab", () => {
  it("sits beside the overview in the study lightbox", () => {
    expect(DIALOG_TABS.map((t) => t.id)).toEqual(["overview", "report", "declaration", "grid", "queue"]);
  });

  it("prints the reading, the advice with its reason, the gates, the chart and the arm table", () => {
    const html = renderToString(<ReportBody report={REPORT} />);
    expect(html).toContain("What the figures say");
    expect(html).toContain("<strong>wide trades better but trades less (design).</strong>");
    expect(html).toContain("What to do next");
    expect(html).toContain(ADVICE_WORD.new_study);
    expect(html).toContain("Why: wide beats the baseline when both trade.");
    expect(html).toContain("Suggestions only");
    expect(html).toContain("Stabilization gate");
    expect(html).toContain("<strong>two</strong>");
    // The first measure is shown, with its t-statistic chart.
    expect(html).toContain("The arm minus its baseline.");
    expect(html).toContain("t-statistic");
    // Net per trade worked out from net per session is marked; the baseline's return on BP beside.
    expect(html).toContain("≈30.00");
    expect(html).toContain("(3.00%)");
  });

  it("says so when there is nothing to act on, and draws no chart without figures", () => {
    const html = renderToString(<ReportBody report={{ ...REPORT, advice: [], arms: [], measures: [] }} />);
    expect(html).toContain("Nothing to act on");
    expect(html).not.toContain("Every arm, by window");
    expect(html).not.toContain("Arm table");
  });

  it("reads each measure off the arm as the orchestrator stated it", () => {
    const a = arm({});
    expect(measureValue(a, "paired")).toBe(2);
    expect(measureValue(a, "paired_both")).toBe(20);
    expect(measureValue(a, "per_risk")).toBe(2);
    expect(measureValue(a, "entry_rate")).toBe(0.4);
    expect(measureT(a, "paired_both")).toBe(3);
    expect(measureT(a, null)).toBeNull();
    expect(windowFill(["design", "holdout"], "holdout")).toBe("var(--chart-orange)");
  });
});

describe("This week", () => {
  it("starts on the Monday of the week, a Monday itself included", () => {
    expect(mondayOf(new Date("2026-11-07T12:00:00Z"))).toBe("2026-11-02"); // Saturday
    expect(mondayOf(new Date("2026-11-08T12:00:00Z"))).toBe("2026-11-02"); // Sunday
    expect(mondayOf(new Date("2026-11-02T09:00:00Z"))).toBe("2026-11-02");
    expect(mondayOf(new Date("2026-01-01T09:00:00Z"))).toBe("2025-12-29");
  });
});
