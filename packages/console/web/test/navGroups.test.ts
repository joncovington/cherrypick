import { describe, it, expect } from "vitest";
import {
  FLIES_SLIDES,
  NAV_DECL,
  navGroups,
  navSlideIds,
  resolveSlide,
} from "../src/lightbox/navGroups";
import { MODULE_ORDER } from "../src/lightbox/moduleOrder";

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

describe("every page's nav declaration", () => {
  // The flies checks above, over every page on the frame -- the suite surfaces included since they
  // moved (2026-09-25). Driven off MODULE_ORDER, so a page added there is covered the day it lands.
  for (const module of MODULE_ORDER) {
    it(`${module}: every tab is unique, reachable from the rail, and every old id lands on a real one`, () => {
      const decl = NAV_DECL[module];
      const ids = navSlideIds(module);
      expect(ids.length).toBeGreaterThan(0);
      expect(new Set(ids).size).toBe(ids.length);
      const { groups, ungrouped, unknown } = navGroups(module, decl.slides);
      expect(ungrouped).toEqual([]);
      expect(unknown).toEqual([]);
      expect(groups.flatMap((g) => g.slides.map((s) => s.id)).sort()).toEqual([...ids].sort());
      for (const [old, replacement] of Object.entries(decl.legacy ?? {})) {
        expect(ids).toContain(replacement);
        expect(ids).not.toContain(old);
      }
    });
  }
});

describe("a module that declares no groups", () => {

  it("groups whatever it is handed into one unlabelled run", () => {
    const slides = [
      { id: "a", label: "a" },
      { id: "b", label: "b" },
    ];
    const { groups, ungrouped, unknown } = navGroups("gex", slides);
    expect(groups).toEqual([{ label: null, slides }]);
    expect(ungrouped).toEqual([]);
    expect(unknown).toEqual([]);
  });
});
