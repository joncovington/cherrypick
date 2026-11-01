import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BacktestArmCards, BacktestCompounded } from "@console/shared";
import { AccountPicker, CompoundedLens, RiskPicker, UnitPicker, lensPoints } from "../src/pages/Backtest/RunCards";

/**
 * The compounded lens (2026-10-31): the module's re-sized curve, labelled a lens and kept apart from
 * the replay's fixed-lot figures. Synthetic figures only.
 */

const LENS: BacktestCompounded = {
  start: 50_000,
  risk_pct: 2,
  years: 3,
  whole: {
    ending: 61_000,
    cagr: 0.0685,
    max_drawdown: 4_000,
    max_drawdown_pct: 0.07,
    stopped_on: null,
    units_min: 1,
    units_max: 2,
    sessions_rounded_to_none: 3,
  },
  fractional: { ending: 63_500, cagr: 0.083, max_drawdown: 4_500, max_drawdown_pct: 0.075, stopped_on: null },
  sessions_unsized: 1,
  path: [
    { session: "2024-01-02", whole: 50_100, fractional: 50_120 },
    { session: "2024-01-03", whole: 49_900, fractional: 49_910 },
    { session: "2024-01-04", whole: 50_300, fractional: 50_350 },
  ],
};

function arm(compounded: BacktestCompounded | null | undefined): BacktestArmCards {
  return { compounded } as unknown as BacktestArmCards;
}

describe("CompoundedLens", () => {
  it("says it is a lens, never a verdict, and shows whole contracts first with fractional as the bound", () => {
    const html = renderToString(<CompoundedLens c={arm(LENS)} />);
    expect(html).toContain("Not what the replay ran, and never part of a verdict");
    expect(html).toContain("2% of the equity");
    expect(html).toContain("CAGR (whole)");
    expect(html).toContain("6.9%"); // whole CAGR
    expect(html).toContain("CAGR (fractional)");
    expect(html).toContain("1–2×");
    expect(html).toContain("3 session(s) left out at whole contracts");
    expect(html).toContain("1 session(s) traded with no recorded risk");
  });

  it("names a stop, and says why the lens is absent", () => {
    const stopped = { ...LENS, whole: { ...LENS.whole, stopped_on: "2024-01-03", cagr: -1 } };
    expect(renderToString(<CompoundedLens c={arm(stopped)} />)).toContain("stopped 2024-01-03");
    expect(renderToString(<CompoundedLens c={arm(null)} />)).toContain("without an account");
    expect(renderToString(<CompoundedLens c={arm(undefined)} />)).toContain("predates it");
  });

  it("says which risk unit sized it", () => {
    expect(renderToString(<CompoundedLens c={arm(LENS)} />)).toContain("its risk their max loss");
    const stop = renderToString(<CompoundedLens c={arm({ ...LENS, unit: "stop" })} />);
    expect(stop).toContain("what its stops lose at the trigger");
    expect(stop).toContain("a gap through the trigger loses more");
  });

  it("draws the whole-contract curve from its running peak", () => {
    expect(lensPoints(LENS.path).map((p) => p.drawdown)).toEqual([0, 200, 0]);
  });
});

describe("the account and risk pickers", () => {
  it("offers each masked account's current net liq as an account", () => {
    const html = renderToString(
      <AccountPicker capital={61234} peakRisk={900} onPick={() => {}} current={[{ account: "****2375", netLiq: 61234 }]} />,
    );
    expect(html).toContain("current ****2375 ($61,234)");
    expect(html).toMatch(/aria-pressed="true"[^>]*>current/);
  });

  it("offers both risk units with the one in force pressed", () => {
    const html = renderToString(<UnitPicker unit="stop" onPick={() => {}} />);
    expect(html).toContain("max loss");
    expect(html).toMatch(/aria-pressed="true"[^>]*>stop distance/);
  });

  it("shows the risk in force as its placeholder, the default when none is chosen", () => {
    expect(renderToString(<RiskPicker risk={null} onPick={() => {}} />)).toContain('placeholder="2%"');
    expect(renderToString(<RiskPicker risk={1.5} onPick={() => {}} />)).toContain('placeholder="1.5%"');
  });
});
