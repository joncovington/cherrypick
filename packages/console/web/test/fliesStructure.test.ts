import { describe, it, expect } from "vitest";
import { structureLabel } from "../src/pages/Flies/structure";

/**
 * Naming a fly position's structure.
 *
 * The regression this pins: both call sites used to end in a default branch that asserted
 * `short ${side}`, so any kind nobody had enumerated rendered as a short vertical. 27 settled
 * `long_vertical` rows were therefore labelled as their own opposite, and `bwb` was a "bwb put" on
 * one card and a "short put" on the other because the two copies had drifted.
 */
describe("the structure label", () => {
  it("names every kind the module actually records", () => {
    // The full set present in the books, paper and live.
    expect(structureLabel("fly", "put")).toBe("fly");
    expect(structureLabel("iron_fly", "put")).toBe("iron fly");
    expect(structureLabel("bwb", "put")).toBe("bwb put");
    expect(structureLabel("short_vertical", "call")).toBe("short call");
    expect(structureLabel("long_vertical", "call")).toBe("long call");
  });

  it("does not call a long vertical a short one", () => {
    // The bug itself, in one assertion.
    expect(structureLabel("long_vertical", "put")).not.toContain("short");
    expect(structureLabel("long_vertical", "put")).toBe("long put");
  });

  it("shows an unfamiliar kind as itself rather than guessing", () => {
    // The root cause was a confident default, not a missing case. A structure the console has never
    // heard of must read as "something new", never as something it is not.
    expect(structureLabel("condor", "put")).toBe("condor");
    expect(structureLabel("ratio_spread", null)).toBe("ratio_spread");
  });

  it("survives a missing side without inventing one", () => {
    expect(structureLabel("short_vertical", null)).toBe("short vertical");
    expect(structureLabel("long_vertical", null)).toBe("long vertical");
    expect(structureLabel("bwb", null)).toBe("bwb");
    expect(structureLabel(null, null)).toBe("—");
  });
});
