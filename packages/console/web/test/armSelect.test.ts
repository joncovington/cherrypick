import { describe, it, expect } from "vitest";
import { visibleArms } from "../src/components/ArmSelect";

describe("which arms an arm filter lists", () => {
  const options = ["control", "callwall", "range-high", "gex"];
  const retired = ["callwall", "gex"];

  it("hides retired arms by default", () => {
    expect(visibleArms(options, retired, false, null)).toEqual(["control", "range-high"]);
  });

  it("lists them all when the toggle is on", () => {
    expect(visibleArms(options, retired, true, null)).toEqual(options);
  });

  it("keeps a retired arm that is already selected, so hiding never changes what the page shows", () => {
    expect(visibleArms(options, retired, false, "callwall")).toEqual(["control", "callwall", "range-high"]);
  });

  it("passes options through untouched while they or the retired list are still loading", () => {
    expect(visibleArms(undefined, retired, false, null)).toBeUndefined();
    expect(visibleArms(options, [], false, null)).toEqual(options);
  });
});
