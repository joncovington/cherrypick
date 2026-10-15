import { describe, it, expect } from "vitest";
import { NAV_DECL, resolveSlide, viewSlides, type ModuleNavDecl } from "../src/lightbox/navGroups";
import type { ModuleId } from "../src/lightbox/moduleOrder";

/** Every `simple` id must name a declared tab, or the simple view silently drops a page. */
function unknownSimpleIds(decls: Record<string, ModuleNavDecl>): string[] {
  const bad: string[] = [];
  for (const [module, decl] of Object.entries(decls)) {
    const ids = new Set(decl.slides.map((s) => s.id));
    for (const id of decl.simple ?? []) if (!ids.has(id)) bad.push(`${module}:${id}`);
  }
  return bad;
}

describe("the simple and detail views", () => {
  it("names only tabs each module actually has", () => {
    expect(unknownSimpleIds(NAV_DECL)).toEqual([]);
  });

  it("the guard fires on a simple id that names no tab", () => {
    const broken = { ...NAV_DECL, meic: { ...NAV_DECL.meic, simple: ["summary", "no-such-tab"] } };
    expect(unknownSimpleIds(broken)).toEqual(["meic:no-such-tab"]);
  });

  it("detail lists every tab; simple lists the module's own set in tab order", () => {
    const slides = NAV_DECL.meic.slides;
    expect(viewSlides("meic", slides, "detail").map((s) => s.id)).toEqual(slides.map((s) => s.id));
    expect(viewSlides("meic", slides, "simple").map((s) => s.id)).toEqual(["summary", "forest", "performance", "positions", "guide"]);
  });

  it("keeps the tab the reader is on, even when the simple view leaves it out", () => {
    const ids = viewSlides("meic", NAV_DECL.meic.slides, "simple", "attempts").map((s) => s.id);
    expect(ids).toContain("attempts");
  });

  it("a module that has not chosen shows everything in both views", () => {
    const gex = "gex" as ModuleId;
    expect(NAV_DECL[gex].simple).toBeUndefined();
    expect(viewSlides(gex, NAV_DECL[gex].slides, "simple")).toHaveLength(NAV_DECL[gex].slides.length);
  });

  it("a bare module URL lands on the view's first tab; a named tab is honoured in either view", () => {
    const slides = NAV_DECL.meic.slides;
    expect(resolveSlide("meic", "", slides, viewSlides("meic", slides, "simple"))).toBe("summary");
    expect(resolveSlide("meic", "", slides, viewSlides("meic", slides, "detail"))).toBe("session");
    expect(resolveSlide("meic", "attempts", slides, viewSlides("meic", slides, "simple"))).toBe("attempts");
  });
});
