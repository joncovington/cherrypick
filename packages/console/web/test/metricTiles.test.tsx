import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MetricTiles } from "../src/components/performance/MetricTiles";

/**
 * `calibration_reading`'s win_rate/return_on_capital/capture_rate are FRACTIONS (0.0567, not
 * 5.67) -- `fmtPct` expects an already-scaled percent, so a call site that forgot to multiply by
 * 100 would silently render "0.06%" for a 6% capture rate. That is exactly the bug this test
 * exists to catch: it was live in an early draft of MetricTiles.tsx before being caught here.
 */

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

const READING = {
  sample: 3,
  net_pnl: 12.5,
  win_rate: 0.6667,
  expectancy: 4.17,
  profit_factor: 1.8,
  sharpe: 0.9,
  max_drawdown: 8.0,
  return_on_capital: 0.0124,
  capture_rate: { value: 0.0567, n: 1 },
};

describe("MetricTiles", () => {
  it("scales win_rate/return_on_capital/capture_rate as percentages, not fractions", () => {
    const html = text(<MetricTiles reading={READING} />);
    expect(html).toContain("66.7%"); // win_rate 0.6667 -> 66.7%, not 0.7%
    expect(html).toContain("1.2%"); // return_on_capital 0.0124 -> 1.2%, not 0.0%
    expect(html).toContain("5.7%"); // capture_rate.value 0.0567 -> 5.7%, not 0.1%
  });

  it("renders max_drawdown as a negative dollar figure", () => {
    const html = text(<MetricTiles reading={READING} />);
    expect(html).toContain("-$8.00");
  });

  it("shows an em-dash rather than crashing on a missing or wrongly-typed key", () => {
    const html = text(<MetricTiles reading={{ sample: "not-a-number" }} />);
    expect(html).toContain("—");
    expect(html).not.toContain("NaN");
  });

  it("attaches n= to a metric with a coverage count, distinct from a metric with none", () => {
    const html = text(<MetricTiles reading={READING} />);
    expect(html).toContain("n=3");
    // capture_rate carries its OWN n (1), separate from the group's overall sample (3).
    expect(html).toContain("n=1");
  });

  it("renders a dozen tiles, so the 2/3/4/6-column grid never ends on a ragged row", () => {
    const html = text(<MetricTiles reading={READING} />);
    expect(html.match(/class="stat-tile"/g)?.length).toBe(12);
    expect(html).toContain("tile-grid-12");
  });

  it("shows the per-session sharpe and PSR over sessions, beside the per-trade one", () => {
    const reading = { ...READING, sessions: 20, session_sharpe: 0.31, psr: 0.9123, worst_session: { session: "2026-09-02", net: -140 } };
    const html = text(<MetricTiles reading={reading} />);
    expect(html).toContain("0.31");
    expect(html).toContain("91.2%"); // psr is a fraction, scaled like win rate
    expect(html).toContain("-$140.00");
    expect(html).toContain("20 sessions");
  });

  it("reads a refused per-session sharpe (too few sessions) as an em-dash, not a zero", () => {
    const html = text(<MetricTiles reading={{ ...READING, sessions: 3, session_sharpe: null, psr: null }} />);
    expect(html).not.toContain("0.0%");
  });
});
