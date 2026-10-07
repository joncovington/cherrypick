import { describe, expect, it } from "vitest";
import { sessionQuery } from "../src/components/Attempts";

/**
 * Attempts, occupancy and meic's divergence card resolve "the latest session" on the server. Under
 * an older era they named the current era's day while the forest named the era's own (2026-10-07),
 * because the era never reached their requests. This pins that it does.
 */

describe("the session-resolved cards' query string", () => {
  it("sends the mode alone when nothing is scoped", () => {
    expect(sessionQuery("paper", null, null)).toBe("mode=paper");
  });

  it("sends the session and the era", () => {
    const q = new URLSearchParams(sessionQuery("live", "2026-08-13", "sample"));
    expect(q.get("mode")).toBe("live");
    expect(q.get("date")).toBe("2026-08-13");
    expect(q.get("era")).toBe("sample");
  });
});
