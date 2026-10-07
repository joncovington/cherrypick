import { describe, expect, it } from "vitest";
import { meicForestQuery } from "../src/pages/Meic/MeicForestCard";

/**
 * The forest's request must carry the page scope, or the header's arm/symbol/era controls move the
 * other MEIC slides while the forest keeps drawing every arm (2026-10-06). The server route reads
 * `date`, `symbol`, `profile` and `era`; this pins that the card sends exactly those.
 */

describe("the meic forest fetch URL carries the page scope", () => {
  it("sends the mode alone when the scope is empty", () => {
    expect(meicForestQuery("paper", { day: null, symbol: null, profile: null, era: null })).toBe("mode=paper");
  });

  it("sends session, symbol, arm and era", () => {
    const q = new URLSearchParams(
      meicForestQuery("live", { day: "2026-09-25", symbol: "NDX", profile: "armA", era: "sample" }),
    );
    expect(q.get("mode")).toBe("live");
    expect(q.get("date")).toBe("2026-09-25");
    expect(q.get("symbol")).toBe("NDX");
    expect(q.get("profile")).toBe("armA");
    expect(q.get("era")).toBe("sample");
  });
});
