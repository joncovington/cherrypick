import { describe, expect, it } from "vitest";
import { attemptsQuery, NO_ATTEMPTS_SCOPE } from "../src/components/Attempts";

/**
 * The attempts request must carry the page scope, or the header's arm/symbol/era controls move the
 * other MEIC slides while the arm rail, timeline and occupancy map keep drawing every arm
 * (2026-10-07). The server route reads `date`, `arm`, `symbol` and `era`; this pins that the cards
 * send exactly those.
 */

describe("the attempts fetch URL carries the page scope", () => {
  it("sends the mode alone when the scope is empty", () => {
    expect(attemptsQuery("paper", null, NO_ATTEMPTS_SCOPE)).toBe("mode=paper");
  });

  it("sends session, arm, symbol and era", () => {
    const q = new URLSearchParams(
      attemptsQuery("live", "2026-10-07", { arm: "control", symbol: "SPX", era: "sample" }),
    );
    expect(q.get("mode")).toBe("live");
    expect(q.get("date")).toBe("2026-10-07");
    expect(q.get("arm")).toBe("control");
    expect(q.get("symbol")).toBe("SPX");
    expect(q.get("era")).toBe("sample");
  });
});
