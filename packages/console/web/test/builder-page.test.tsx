import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderAutomation } from "@console/shared";
import { BuilderCard, SuggestionLine } from "../src/pages/Builder/BuilderPage";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

const BASE: BuilderAutomation = {
  name: "mine",
  group: "ensemble",
  symbol: "SPX",
  enabled: false,
  note: null,
  start: null,
  register: { item: 75, verdict: "no_verdict" },
  summary: "structure backtested (item 75, no_verdict); exit changed since its backtest",
  blocks: [
    { block: "structure", state: "backtested", keys: { symbol: "SPX" } },
    { block: "exit", state: "changed", keys: { exit: { close_at_dte: 1 } } },
    { block: "size", state: "not_backtested", keys: {} },
  ],
  forward: null,
  suggestion: null,
};

describe("the builder card", () => {
  it("labels each block for what a backtest judged of it", () => {
    const out = html(<BuilderCard a={BASE} />);
    expect(out).toContain('class="chip chip-ok">backtested</span>');
    expect(out).toContain('class="chip chip-warn">changed since its backtest</span>');
    expect(out).toContain('class="chip">not backtested</span>');
    expect(out).toContain("{&quot;close_at_dte&quot;:1}");
    expect(out).toContain("item 75: no verdict");
    expect(out).toContain("start not set");
    expect(out).toContain("forward check not declared");
    expect(out).toContain('class="chip">off</span>');
  });

  it("shows the start and the forward rule written at enrolment", () => {
    const out = html(<BuilderCard a={{ ...BASE, start: "2026-11-18", forward: { measure: "session_sum", field: "net_pnl", min_obs: 30 } }} />);
    expect(out).toContain("start 2026-11-18");
    expect(out).toContain("forward check session_sum of net_pnl, 30 to read");
  });

  it("says whether our own data covers a backtest, and the nearest store when not", () => {
    const yes = html(<SuggestionLine s={{ blocks: ["exit"], window: ["2025-11-13", "2026-11-13"], needs: [], possible: true }} />);
    expect(yes).toContain('data-possible="yes"');
    expect(yes).toContain("Our own data covers a backtest of exit from 2025-11-13 to 2026-11-13.");
    const no = html(
      <SuggestionLine
        s={{
          blocks: ["exit"],
          window: ["2025-11-13", "2026-11-13"],
          possible: false,
          needs: [
            { field: "chain", expiry: "0dte", cadence: "1m", covered: false, source: null,
              nearest: { source: "recorded-0dte", first: "2026-10-13", last: "2026-11-13", why: "starts 2026-10-13" } },
            { field: "spot", cadence: "1m", covered: true, source: "spot-trail" },
          ],
        }}
      />,
    );
    expect(no).toContain('data-possible="no"');
    expect(no).toContain("chain (0dte) at 1m");
    expect(no).not.toContain("spot at 1m");
    expect(no).toContain("nearest for chain: recorded-0dte 2026-10-13 to 2026-11-13 (starts 2026-10-13)");
  });
});
