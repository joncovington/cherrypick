import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BacktestArmCards, BacktestSizing } from "@console/shared";
import { PercentPicker, SizingCheck } from "../src/pages/Backtest/RunCards";

/**
 * The sizing check (2026-10-31): the module's allowed size under a drawdown cap, labelled a check and
 * never a change to live config. Synthetic figures only.
 */

const SIZING: BacktestSizing = {
  account: 20_000,
  dd_pct: 30,
  insufficient: false,
  units: 1,
  drawdown_p99_per_unit: 5_900,
  horizon_sessions: 100,
  evidence_sessions: 672,
  overshoot: { n: 241, p50: 1.067, p95: 1.292, p99: 1.71 },
  unit_risk_max_loss: 1_190,
  unit_risk_stop_tail: 1_090,
  risk_pct_max_loss: 5.95,
  risk_pct_stop_tail: 5.45,
};

function arm(sizing: BacktestSizing | null | undefined): BacktestArmCards {
  return { sizing } as unknown as BacktestArmCards;
}

describe("SizingCheck", () => {
  it("states the size the cap allows, both risk readings for MEIC, and that it changes nothing live", () => {
    const html = renderToString(<SizingCheck c={arm(SIZING)} module="meic" onLensAt={() => {}} />);
    expect(html).toContain("never a change to live config");
    expect(html).toContain("within 30% of $20,000");
    expect(html).toContain("29.5%"); // 5,900 / 20,000: one unit's bad-tail drawdown
    expect(html).toContain("5.5%"); // stop + overshoot
    expect(html).toContain("6.0%"); // max loss
    expect(html).toContain("7% / 29%"); // overshoot p50 / p95 past the trigger
    expect(html).toContain("show the compounded lens at this risk (5.5%, stop distance)");
  });

  it("shows no stop reading for a module without stops", () => {
    const html = renderToString(<SizingCheck c={arm(SIZING)} module="flies" onLensAt={() => {}} />);
    expect(html).not.toContain("stop + overshoot");
    expect(html).toContain("show the compounded lens at this risk (6.0%)");
  });

  it("says when none fits, when history is too short, and when no account is chosen", () => {
    const none = renderToString(<SizingCheck c={arm({ ...SIZING, units: 0 })} module="meic" onLensAt={() => {}} />);
    expect(none).toContain("none fits");
    expect(none).not.toContain("show the compounded lens");
    const short = { account: 20_000, dd_pct: 30, insufficient: true, evidence: 40, min_evidence: 100 };
    expect(renderToString(<SizingCheck c={arm(short)} />)).toContain("40 traded sessions, the bootstrap needs 100");
    expect(renderToString(<SizingCheck c={arm(null)} />)).toContain("Choose an account above");
  });
});

describe("PercentPicker", () => {
  it("shows the value in force, the default when none is chosen", () => {
    expect(renderToString(<PercentPicker label="drawdown cap" aria="x" value={null} fallback={30} onPick={() => {}} />)).toContain('placeholder="30%"');
    expect(renderToString(<PercentPicker label="drawdown cap" aria="x" value={25} fallback={30} onPick={() => {}} />)).toContain('placeholder="25%"');
  });
});
