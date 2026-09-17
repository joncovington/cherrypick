import { describe, it, expect } from "vitest";
import { adviceDeclOf, advisedTagStatus } from "../src/readers/adviceDecl.js";
import { advisedTagFor, resolveAdvisedTag, slugExperimentName, type ExperimentRef } from "../src/readers/experimentIndex.js";

/**
 * The advised books are the one tag class no arm/profile registry knows about — the paper loops
 * synthesize them from the module config's advice block — so both status surfaces (Champions, the
 * Experiment Guide) classify them from that block and the advisor's own experiment rows instead.
 * These pin the rule that caught us out on 2026-08-18: advised:width-5 badged "retired" while
 * taking 235 entries a day — and, since 2026-09-17, that a module can show several active advised
 * tags at once, one per experiment.
 */

function exp(over: Partial<ExperimentRef> & { id: string }): ExperimentRef {
  return { module: "meic", baseProfile: "control", name: null, tag: null, status: "active", underpowered: null, ...over };
}

describe("advised tag status without an advisor store (the pre-2026-09-17 rule)", () => {
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

describe("advised tag status against the advisor's experiment rows", () => {
  const decl = adviceDeclOf({ advice: { enabled: true, base_profile: "control" } }, "base_profile");
  const index: ExperimentRef[] = [
    exp({ id: "exp-a", name: "entry-window-truncate-vs-control", tag: "advised:entry-window-truncate-vs-control" }),
    exp({ id: "exp-b", name: "Stop Later", tag: advisedTagFor("Stop Later") }),
    exp({ id: "exp-old", name: "gex-gate-earns-its-keep", tag: "advised:gex-gate-earns-its-keep", status: "killed" }),
  ];

  it("several advised tags are active at once, one per active experiment", () => {
    expect(advisedTagStatus("advised:entry-window-truncate-vs-control", decl, index)).toBe("active");
    expect(advisedTagStatus("advised:stop-later", decl, index)).toBe("active");
  });

  it("a concluded experiment's book is retired even though advice is on", () => {
    expect(advisedTagStatus("advised:gex-gate-earns-its-keep", decl, index)).toBe("retired");
  });

  it("the rows' stamp outranks the tag", () => {
    // Legacy rows under advised:control stamped with a live experiment: that experiment's book.
    expect(advisedTagStatus("advised:control@exp-a", decl, index)).toBe("active");
    expect(advisedTagStatus("advised:control@exp-old", decl, index)).toBe("retired");
  });

  it("a legacy advised:<base> tag no experiment claims is history once a store exists", () => {
    // The pre-change rule would have called this active (advice on, control is the base): with
    // rows to ask, the honest reading is that no running experiment writes this book.
    expect(advisedTagStatus("advised:control", decl, index)).toBe("retired");
  });

  it("a legacy tag stays active only for an active experiment with no name or tag to resolve by", () => {
    const nameless = [exp({ id: "exp-n", name: null, tag: null, baseProfile: "control" })];
    expect(advisedTagStatus("advised:control", decl, nameless)).toBe("active");
    expect(advisedTagStatus("advised:width-5", decl, nameless)).toBe("retired");
  });

  it("advice switched off in the module config retires every book, running experiment or not", () => {
    const off = adviceDeclOf({ advice: { enabled: false, base_profile: "control" } }, "base_profile");
    expect(advisedTagStatus("advised:entry-window-truncate-vs-control", off, index)).toBe("retired");
  });

  it("an unreadable declaration defers to the rows rather than reporting unknown", () => {
    expect(advisedTagStatus("advised:entry-window-truncate-vs-control", null, index)).toBe("active");
    expect(advisedTagStatus("advised:control", null, [])).toBe("retired");
  });
});

describe("the experiment index's tag rules", () => {
  it("slugs exactly as cherrypick.core.advice.slug does", () => {
    expect(slugExperimentName("Forecast Range (floor probe)")).toBe("forecast-range-floor-probe");
    expect(slugExperimentName("  entry-window-truncate-vs-control ")).toBe("entry-window-truncate-vs-control");
    expect(slugExperimentName(null)).toBe("");
    expect(advisedTagFor("Iron Fly take earlier", "iron_fly")).toBe("advised:iron-fly-take-earlier:iron_fly");
  });

  it("resolves the base from the experiment row, keeping earnings' strategy suffix", () => {
    const index = [exp({ id: "exp-e", module: "earnings", baseProfile: "strat_test", name: "ironfly-take-earlier", tag: "advised:ironfly-take-earlier" })];
    expect(resolveAdvisedTag("advised:ironfly-take-earlier:iron_fly", index)).toMatchObject({
      base: "strat_test:iron_fly",
      attribution: "tag",
      strategy: "iron_fly",
    });
    expect(resolveAdvisedTag("advised:strat_test:iron_fly@exp-e", index)).toMatchObject({ base: "strat_test:iron_fly", attribution: "stamp" });
    expect(resolveAdvisedTag("advised:balanced:iron_fly", index)).toMatchObject({ base: "balanced:iron_fly", attribution: "none", experiment: null });
  });
});
