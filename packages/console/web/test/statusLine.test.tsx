import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { StatusLine } from "../src/components/summary/ModuleSummary";
import { RejectedNotice } from "../src/lightbox/manifests/FlowLightbox";
import { TESTED_STATUS } from "../src/lib/testedStatus";

const renderToText = (el: Parameters<typeof renderToString>[0]) => renderToString(el).replace(/<!-- -->/g, "");

/**
 * Batch 1 of docs/console-review-plan.md, "say what is true": each module's summary opens with which
 * ledger it is, whether live has traded, a stale day labelled as the last traded one, and what
 * history says; Flow says when a later capture was refused.
 */

describe("module status line", () => {
  it("states the ledger and the tested status in words", () => {
    const html = renderToText(<StatusLine module="contango" mode="paper" />);
    expect(html).toContain("Paper.");
    expect(html).toContain("drawdown, not return");
    expect(html).toContain(TESTED_STATUS["contango"]!.source);
  });

  it("says live has not traded, rather than an empty window", () => {
    expect(renderToText(<StatusLine module="bwb" mode="live" neverLive />)).toContain("Live has not traded yet.");
    expect(renderToText(<StatusLine module="bwb" mode="live" />)).not.toContain("not traded yet");
  });

  it("labels an older latest day as the last traded day", () => {
    const html = renderToText(<StatusLine module="curve" mode="paper" lastTraded="2026-10-07" />);
    expect(html).toContain("Nothing traded today");
    expect(html).toContain("October 7, 2026");
  });

  it("says nothing about testing for a module with no entry, rather than guess", () => {
    expect(renderToText(<StatusLine module="flies" mode="paper" />)).not.toContain("(");
  });

  it("covers every module that keeps a summary page", () => {
    for (const m of ["meic", "bwb", "earnings", "pmcc", "calendars", "curve", "contango"]) expect(TESTED_STATUS[m]).toBeDefined();
  });
});

describe("refused flow capture", () => {
  const rejected = { session: "2026-10-29", savedAt: null, problems: ["spreads GE: 4.34 x 10000 x 100 != premium 2,170,217"] };

  it("names the refused day, the day shown and each failed check", () => {
    const html = renderToText(<RejectedNotice rejected={rejected} shown="2026-10-28" />);
    expect(html).toContain("The 2026-10-29 capture was refused");
    expect(html).toContain("2026-10-28 is the latest capture that passed");
    expect(html).toContain("spreads GE");
  });
});
