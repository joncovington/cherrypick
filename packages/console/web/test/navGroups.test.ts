import { describe, it, expect } from "vitest";
import {
  FLIES_SLIDES,
  NAV_DECL,
  navGroups,
  navSlideIds,
  resolveSlide,
} from "../src/lightbox/navGroups";

/**
 * The nav declaration is a second copy of something each manifest also states, so the failures
 * here are the quiet kind: a slide in no group simply does not appear in the rail, and a group
 * naming a slide that does not exist renders one fewer link. Neither throws, neither logs, and
 * both read as "that tab was removed on purpose".
 *
 * Shown to fail before this landed, which is the only reason to trust it:
 *   - dropping "openingrange" from the `today` group     -> ungrouped: ["openingrange"]
 *   - adding a stray "books" id to the `evidence` group  -> unknown:   ["books"]
 * Both were reverted; the assertions below are what caught them.
 */

describe("the flies nav declaration", () => {
  const decl = NAV_DECL.flies!;

  it("puts every declared slide in exactly one group", () => {
    const { groups, ungrouped, unknown } = navGroups("flies", decl.slides);
    expect(ungrouped).toEqual([]);
    expect(unknown).toEqual([]);
    const placed = groups.flatMap((g) => g.slides.map((s) => s.id));
    expect(placed.length).toBe(decl.slides.length);
    expect(new Set(placed).size).toBe(decl.slides.length);
  });

  it("names every slide exactly once", () => {
    const ids = FLIES_SLIDES.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every legacy id points at a tab that exists, and shadows none", () => {
    const ids = new Set(navSlideIds("flies"));
    for (const [old, replacement] of Object.entries(decl.legacy ?? {})) {
      // An alias to a tab that was itself renamed later would send readers nowhere.
      expect(ids.has(replacement)).toBe(true);
      // An alias whose key is also a live tab would never be consulted — a rename undone by
      // accident, looking like it still works.
      expect(ids.has(old)).toBe(false);
    }
  });
});

describe("resolveSlide", () => {
  const slides = NAV_DECL.flies!.slides;

  it("passes a live id straight through", () => {
    expect(resolveSlide("flies", "forest", slides)).toBe("forest");
  });

  it("sends a renamed tab's old id to its replacement", () => {
    expect(resolveSlide("flies", "exits", slides)).toBe("divergence");
    expect(resolveSlide("flies", "now", slides)).toBe("session");
  });

  it("falls back to the first tab for an id that means nothing", () => {
    expect(resolveSlide("flies", "no-such-slide", slides)).toBe("session");
    expect(resolveSlide("flies", "", slides)).toBe("session");
  });
});

describe("a module with no declaration", () => {
  it("has no static slides, so keyboard stepping declines rather than guessing", () => {
    expect(navSlideIds("pmcc")).toEqual([]);
  });

  it("groups whatever it is handed into one unlabelled run", () => {
    const slides = [
      { id: "a", label: "a" },
      { id: "b", label: "b" },
    ];
    const { groups, ungrouped, unknown } = navGroups("pmcc", slides);
    expect(groups).toEqual([{ label: null, slides }]);
    expect(ungrouped).toEqual([]);
    expect(unknown).toEqual([]);
  });
});
