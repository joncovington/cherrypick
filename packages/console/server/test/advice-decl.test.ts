import { describe, it, expect } from "vitest";
import { adviceDeclOf, advisedTagStatus } from "../src/readers/adviceDecl.js";

/**
 * The advised books (`advised:<base>`) are the one tag class no arm/profile registry knows about —
 * the paper loops synthesize them from the module config's advice block — so both status surfaces
 * (Champions, the Experiment Guide) classify them from that block instead. These pin the rule that
 * caught us out on 2026-08-18: advised:width-5 badged "retired" while taking 235 entries a day.
 */
describe("advised tag status", () => {
  const decl = adviceDeclOf({ advice: { enabled: true, base_profile: "width-5" } }, "base_profile");

  it("the book the advice block currently produces is active", () => {
    expect(advisedTagStatus("advised:width-5", decl)).toBe("active");
  });

  it("a book the advice block no longer points at is retired", () => {
    // advised:control stopped being produced when MEIC's base was re-pointed at width-5.
    expect(advisedTagStatus("advised:control", decl)).toBe("retired");
  });

  it("advice off retires every advised book, including the declared base's", () => {
    const off = adviceDeclOf({ advice: { enabled: false, base_profile: "width-5" } }, "base_profile");
    expect(advisedTagStatus("advised:width-5", off)).toBe("retired");
  });

  it("no readable declaration is unknown, never a guessed retirement", () => {
    expect(advisedTagStatus("advised:width-5", null)).toBe("unknown");
    expect(advisedTagStatus("advised:width-5", adviceDeclOf({}, "base_profile"))).toBe("unknown");
    expect(advisedTagStatus("advised:width-5", adviceDeclOf(null, "base_profile"))).toBe("unknown");
  });

  it("flies keys its base differently, and the wrong key reads as no base", () => {
    const flies = adviceDeclOf({ advice: { enabled: true, base_arm: "control" } }, "base_arm");
    expect(advisedTagStatus("advised:control", flies)).toBe("active");
    const wrongKey = adviceDeclOf({ advice: { enabled: true, base_arm: "control" } }, "base_profile");
    expect(advisedTagStatus("advised:control", wrongKey)).toBe("retired");
  });
});
