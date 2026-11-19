import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderStrategySchema } from "@console/shared";
import { startSettings, StrategyPicker, targetLabel } from "../src/pages/Builder/StrategyPicker";
import { structureName } from "../src/pages/Builder/BuilderForm";

const SCHEMA: BuilderStrategySchema = {
  strategies: {
    vertical: { label: "Vertical", about: "Two options of one right.", settings: ["direction", "right", "dte", "place_by", "target", "width"], available: true, why: null },
    iron_condor: { label: "Iron condor", about: "A put spread and a call spread.", settings: ["direction", "dte", "place_by", "target", "width"], available: true, why: null },
    calendar: { label: "Calendar", about: "Two expiries.", settings: [], available: false, why: "needs named expiries: a leg on a second expiry" },
    custom: { label: "Custom", about: "Any legs.", settings: ["dte"], available: true, why: null },
  },
  settings: {
    direction: { type: "enum", options: ["short", "long"], default: "short" },
    right: { type: "enum", options: ["put", "call"], default: "put" },
    dte: { type: "int", min: 0, max: 400, default: 45 },
    place_by: { type: "enum", options: ["delta", "pct", "sd", "points"], default: "delta" },
    target: { type: "number", above: 0, default: 0.16 },
    width: { type: "number", above: 0, default: 5 },
  },
  target_defaults: { delta: 0.16, pct: 2, sd: 1, points: 10 },
};

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

describe("the strategy selector", () => {
  it("lists every strategy the module offers, Custom included, and greys one it cannot build yet with why", () => {
    const out = html(<StrategyPicker schema={SCHEMA} onFill={() => {}} initial="iron_condor" />);
    for (const label of ["Vertical", "Iron condor", "Calendar", "Custom"]) expect(out).toContain(label);
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*title="needs named expiries: a leg on a second expiry"/);
    expect(out).toContain("is-custom");
    expect(out).toMatch(/aria-checked="true"[^>]*class="strat-item is-on"><span class="strat-name">Iron condor/);
  });

  it("shows only the settings the chosen strategy takes, the target named by how it is placed", () => {
    const out = html(<StrategyPicker schema={SCHEMA} onFill={() => {}} initial="iron_condor" />);
    expect(out).toContain("target delta");
    expect(out).not.toContain(">side<");
    expect(out).toContain("Fill in the legs");
    expect(targetLabel("sd")).toBe("standard deviations");
  });

  it("starts each strategy from the module's defaults", () => {
    expect(startSettings(SCHEMA, "vertical")).toEqual({ direction: "short", right: "put", dte: 45, place_by: "delta", target: 0.16, width: 5 });
  });

  it("applies as it changes in the wizard, so it shows no button there", () => {
    expect(html(<StrategyPicker schema={SCHEMA} onFill={() => {}} auto />)).not.toContain("Fill in the legs");
  });
});

describe("the structure's name", () => {
  it("is the strategy filled in, Custom once its legs are edited, Custom when none filled them", () => {
    const legs = [{ name: "L1" }];
    expect(structureName({ label: "Iron condor", legs: JSON.stringify(legs) }, legs)).toBe("Iron condor");
    expect(structureName({ label: "Iron condor", legs: JSON.stringify(legs) }, [{ name: "L1" }, { name: "L2" }])).toBe("Custom (from Iron condor)");
    expect(structureName(null, legs)).toBe("Custom");
  });
});
