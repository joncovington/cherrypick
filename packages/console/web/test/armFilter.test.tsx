import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ArmAnalysis, RegimeArm, RegimeCuts } from "@console/shared";
import { armOfTag, matchesArm } from "../src/lib/armTag";
import { cutsForArm } from "../src/components/RegimeCutsTab";
import { ArmPicker, pickedArm } from "../src/components/analysis/AnalysisSlide";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

describe("the arm picker's tag rule", () => {
  it("keeps an advised book with the arm it ran under", () => {
    expect(armOfTag("advised:x@exp-2026-10-01-flies-1")).toBe("advised:x");
    expect(armOfTag("control")).toBe("control");
    expect(matchesArm("advised:x@exp-1", "advised:x")).toBe(true);
    expect(matchesArm("control", "advised:x")).toBe(false);
  });

  it("passes everything when no arm is picked", () => {
    expect(matchesArm("anything", null)).toBe(true);
    expect(matchesArm("anything", undefined)).toBe(true);
  });
});

describe("cutsForArm", () => {
  const arm = (name: string) => ({ arm: name }) as RegimeArm;
  const cuts = {
    arms: [arm("control"), arm("range-high")],
    crossTabs: [{ dims: ["gex", "trend"], arms: [{ arm: "control", cells: [] }, { arm: "range-high", cells: [] }] }],
    multiplicity: { intervals: 40 },
  } as unknown as RegimeCuts;

  it("narrows every table and cross-tab to the picked arm, and leaves document stamps alone", () => {
    const one = cutsForArm(cuts, "range-high");
    expect(one.arms.map((a) => a.arm)).toEqual(["range-high"]);
    expect(one.crossTabs[0]?.arms.map((a) => a.arm)).toEqual(["range-high"]);
    expect(one.multiplicity).toBe(cuts.multiplicity);
  });

  it("is the whole cut when no arm is picked", () => {
    expect(cutsForArm(cuts, null)).toBe(cuts);
  });
});

describe("ArmPicker", () => {
  const g = (tag: string, net: number) => ({
    tag,
    analysis: {
      reading: { net_pnl: net },
      stress: { sessions: 12 },
      coverage: { trades: 30 },
      drawdowns: { deepest: [{ depth: -400 }] },
      worst_trade: { net: -120 },
    } as unknown as ArmAnalysis,
  });

  it("lists every arm with its net toned by sign, its deepest fall as a loss, and marks the picked one", () => {
    const html = text(<ArmPicker groups={[g("control", 1545.88), g("range-high", -2795.66)]} picked="range-high" onPick={() => {}} />);
    expect(html).toMatch(/class="num pnl-pos">\$1545\.88/);
    expect(html).toMatch(/class="num pnl-neg">-\$2795\.66/);
    expect(html).toContain('aria-pressed="true" title="Show range-high');
    expect(html).toContain('aria-pressed="false" title="Show control');
    expect(html).toContain(">30<");
  });

  it("shows the address's arm when it was read, else the first", () => {
    expect(pickedArm(["control", "range-high"], "range-high")).toBe("range-high");
    expect(pickedArm(["control", "range-high"], "gone")).toBe("control");
    expect(pickedArm(["control"], null)).toBe("control");
    expect(pickedArm([], "control")).toBeNull();
  });
});
