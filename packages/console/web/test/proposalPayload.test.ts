import { describe, expect, it } from "vitest";
import { otherFields, paramRows, scalar } from "../src/pages/Advisor/proposalPayload";

/**
 * These mirror `packages/advisor/tests/test_proposals.py`, on purpose. A proposal is stored as the
 * model's raw reply, so the console reads shapes the Python parser has already accepted — and the
 * failure when the two disagree is silent on this side: an admitted proposal renders with no
 * statement of what it changes, which is exactly the thing the page exists to show.
 */

describe("paramRows", () => {
  it("reads the list of {param, value} the prompt asks for", () => {
    expect(
      paramRows([{ param: "stop_trigger_ratio", value: 1.2, rationale: "one lever only" }]),
    ).toEqual([{ param: "stop_trigger_ratio", value: 1.2, rationale: "one lever only" }]);
  });

  it("reads the plain {param: value} map, which carries no rationale", () => {
    expect(paramRows({ stop_trigger_ratio: 0.92, min_iv_rank: 30 })).toEqual([
      { param: "stop_trigger_ratio", value: 0.92, rationale: null },
      { param: "min_iv_rank", value: 30, rationale: null },
    ]);
  });

  it("reads one bare entry as an entry, not as a map of its own field names", () => {
    // Read as a map this is three params called param, value and rationale — the card would name
    // parameters the model never proposed.
    expect(paramRows({ param: "min_floor_dollars", value: 10, rationale: "make it bind" })).toEqual([
      { param: "min_floor_dollars", value: 10, rationale: "make it bind" },
    ]);
  });

  it("still reads a map whose keys merely resemble an entry's", () => {
    expect(paramRows({ value: 3, rationale: 4 })).toEqual([
      { param: "value", value: 3, rationale: null },
      { param: "rationale", value: 4, rationale: null },
    ]);
  });

  it("separates an absent params field from an empty one", () => {
    expect(paramRows(undefined)).toBeNull();
    expect(paramRows(null)).toBeNull();
    expect(paramRows("nonsense")).toBeNull();
    expect(paramRows([])).toEqual([]);
  });

  it("keeps a row whose rationale is missing or not a string", () => {
    expect(paramRows([{ param: "fee_buffer", value: 0.1 }, { param: "x", value: 1, rationale: 7 }])).toEqual([
      { param: "fee_buffer", value: 0.1, rationale: null },
      { param: "x", value: 1, rationale: null },
    ]);
  });
});

describe("otherFields", () => {
  it("is empty when the card renders every key itself", () => {
    expect(
      otherFields({
        kind: "experiment_spec", module: "meic", name: "stop-later", hypothesis: "…",
        success_metric: "…", sessions: 15, params: [],
      }),
    ).toEqual([]);
  });

  it("surfaces a key the card has no opinion about", () => {
    expect(otherFields({ kind: "tune", params: [], confidence: 0.8 })).toEqual([["confidence", 0.8]]);
  });

  it("refuses to walk a payload that is not an object", () => {
    // A malformed proposal stores whatever the model sent. Object.entries on a string is a table
    // of its characters, so the caller shows it whole instead.
    expect(otherFields("not even an object")).toBeNull();
    expect(otherFields(["a", "b"])).toBeNull();
  });
});

describe("scalar", () => {
  it("renders nested values as JSON rather than [object Object]", () => {
    expect(scalar({ a: 1 })).toBe('{"a":1}');
    expect(scalar([1, 2])).toBe("[1,2]");
    expect(scalar(null)).toBe("—");
    expect(scalar(undefined)).toBe("—");
    expect(scalar(false)).toBe("false");
    expect(scalar("plain")).toBe("plain");
  });
});
