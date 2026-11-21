import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { Scorecard, ScorecardRow } from "@console/shared";
import { ScorecardBody, rowHref } from "../src/pages/Compare/ScorecardTab";

/** Compare's scorecard: Python's rows and looks laid out, each linked to its page, the look filter. */

const html = (doc: Scorecard, onlyLooks = false) =>
  renderToString(
    <MemoryRouter>
      <ScorecardBody doc={doc} onlyLooks={onlyLooks} />
    </MemoryRouter>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

function row(over: Partial<ScorecardRow>): ScorecardRow {
  return {
    module: "meic",
    arm: "x",
    kind: "arm",
    research: null,
    vs_backtest: null,
    vs_control: null,
    sample: { basis: "sessions", have: 14, need: null },
    sessions: 14,
    trades: 30,
    net: 120,
    max_drawdown: 80,
    running: true,
    look: null,
    look_words: null,
    ...over,
  };
}

const DOC: Scorecard = {
  rows: [
    row({
      arm: "pm1400-n3",
      look: "forward_drift",
      look_words: "its forward check reads drift: below its backtest",
      research: { id: 73, verdict: "passed" },
      vs_backtest: { id: 73, study: "item73", state: "drift", withheld: false, n: 41, min_obs: 40 },
      vs_control: { against: "control", reading: "leaning", direction: "worse", sessions: 14, sessions_to_decision: 6 },
      sample: { basis: "forward", have: 41, need: 40 },
      net: -300,
    }),
    row({ module: "automations", arm: "iron-condor-1345", kind: "automation", vs_backtest: { id: 75, study: "item75", state: null, withheld: true, n: 31, min_obs: 30 } }),
    row({ arm: "control" }),
    row({ module: "curve", arm: "c1" }),
  ],
  eras: {},
  looks: [{ key: "forward_drift", words: "its forward check reads drift: below its backtest" }],
  counts: { forward_drift: 1 },
  min_sessions: 10,
  modules: ["meic", "automations", "curve"],
  error: null,
};

describe("the scorecard", () => {
  it("links each row to its own page where it has one", () => {
    expect(rowHref({ module: "meic", arm: "pm1400-n3" })).toBe("/meic/arm/pm1400-n3");
    expect(rowHref({ module: "automations", arm: "a b" })).toBe("/automations/automation/a%20b");
    expect(rowHref({ module: "curve", arm: "c1" })).toBeNull();
  });

  it("renders Python's readings and look, with the count above", () => {
    const out = html(DOC);
    expect(out).toContain("4 arms and automations; 1 need a look · 1 its forward check reads drift: below its backtest.");
    expect(out).toContain('<a href="/meic/arm/pm1400-n3">pm1400-n3</a>');
    expect(out).toContain('<span class="chip chip-warn">drift</span>');
    expect(out).toContain("leaning");
    expect(out).toContain("worse than control");
    expect(out).toContain("41 of 40");
    expect(out).toContain("withheld (licensed)");
    expect(out).toContain("is a control");
    expect(out).toContain('<td class="sc-arm">c1<div class="muted sc-module">curve</div></td>');
  });

  it("filters to the rows with a look, and says so when none has one", () => {
    const only = html(DOC, true);
    expect(only).toContain("pm1400-n3");
    expect(only).not.toContain("iron-condor-1345");
    const none = html({ ...DOC, rows: DOC.rows.slice(1), counts: {} }, true);
    expect(none).toContain("none needs a look");
    expect(none).toContain("Nothing needs a look.");
  });

  it("says why when the read failed", () => {
    expect(html({ ...DOC, error: "the scorecard failed" })).toContain('<p class="error">the scorecard failed</p>');
  });
});
