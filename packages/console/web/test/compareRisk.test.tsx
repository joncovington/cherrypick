import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ArmRiskRow, ArmsRiskResult } from "@console/shared";
import { RiskTable } from "../src/pages/Compare/RiskTab";

const text = (node: React.ReactElement) => renderToString(<MemoryRouter>{node}</MemoryRouter>).replace(/<!--\s*-->/g, "");

function row(over: Partial<ArmRiskRow>): ArmRiskRow {
  return {
    module: "flies", tag: "control",
    window: { scope: "arm_era", start: "2026-10-05", break: null, reason: "the module's latest book-wide break" },
    trades: 83, sessions: 9, net: -2890.62, peakRisk: 2162.85, netOverLargestPeak: -1.3365, maxDrawdown: 3379.47,
    drawdownOverPeak: 1.5625, stressBlocks: null, stressWithheld: "9 sessions, fewer than 20", withoutTop10: -6000,
    top10SignFlips: false, currentRun: { kind: "loss", length: 3 }, breakevenMultiple: null, ...over,
  };  // fmt: skip
}

const data: ArmsRiskResult = {
  mode: "paper",
  rows: [
    row({}),
    row({ module: "meic", tag: "bp-25k", net: 15645.3, withoutTop10: -200, top10SignFlips: true, breakevenMultiple: 17.07 }),
    row({ module: "pmcc", tag: "control", net: -1085.84 }),
  ],
  failures: [{ module: "curve", error: "cannot read" }],
};

describe("the Compare risk table", () => {
  it("shows every visible arm, each with its window, and a drawdown as the loss it is", () => {
    const html = text(<RiskTable data={data} visible={() => true} />);
    expect(html).toContain("2026-10-05");
    expect(html).toContain(" · era");
    expect(html).toContain("-$3379.47");
    expect(html).toContain("17.1×");
    expect(html).toContain(">sign flips<");
  });

  it("hides a module that is switched off, its failure included", () => {
    const html = text(<RiskTable data={data} visible={(m) => m !== "pmcc" && m !== "curve"} />);
    expect(html).not.toContain(">pmcc<");
    expect(html).not.toContain("could not be read");
  });

  it("names a module whose read failed", () => {
    const html = text(<RiskTable data={data} visible={() => true} />);
    expect(html).toContain("could not be read — cannot read");
  });
});
