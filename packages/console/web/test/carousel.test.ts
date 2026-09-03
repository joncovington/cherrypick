import { describe, it, expect } from "vitest";
import { nextSlideId, prevSlideId, firstSlideId, lastSlideId, nextModule, prevModule } from "../src/lightbox/useCarousel";
import { MODULE_ORDER } from "../src/lightbox/moduleOrder";
import type { SlideDef } from "../src/lightbox/types";

const noop = () => null;
function slides(...ids: Array<string | [string, boolean]>): SlideDef[] {
  return ids.map((entry) => {
    const [id, available] = Array.isArray(entry) ? entry : [entry, true];
    return { id, label: id, render: noop, available };
  });
}

describe("within-module stepping", () => {
  it("next/prev move one slide at a time", () => {
    const s = slides("now", "forest", "occupancy");
    expect(nextSlideId(s, "now")).toBe("forest");
    expect(nextSlideId(s, "forest")).toBe("occupancy");
    expect(prevSlideId(s, "occupancy")).toBe("forest");
    expect(prevSlideId(s, "forest")).toBe("now");
  });

  it("next at the last slide, and prev at the first, return null (the caller crosses modules)", () => {
    const s = slides("now", "forest");
    expect(nextSlideId(s, "forest")).toBeNull();
    expect(prevSlideId(s, "now")).toBeNull();
  });

  it("an unknown current id is treated as the first slide", () => {
    const s = slides("now", "forest");
    expect(nextSlideId(s, "does-not-exist")).toBe("forest");
  });

  it("first/last name the ends of the list", () => {
    const s = slides("now", "forest", "occupancy");
    expect(firstSlideId(s)).toBe("now");
    expect(lastSlideId(s)).toBe("occupancy");
  });
});

describe("unavailable slides are skipped, never landed on", () => {
  it("next steps over an unavailable slide", () => {
    const s = slides("now", ["timeline", false], "journal");
    expect(nextSlideId(s, "now")).toBe("journal");
  });

  it("prev steps over an unavailable slide", () => {
    const s = slides("now", ["timeline", false], "journal");
    expect(prevSlideId(s, "journal")).toBe("now");
  });

  it("first/last skip a leading/trailing unavailable slide", () => {
    const s = slides(["now", false], "forest", ["guide", false]);
    expect(firstSlideId(s)).toBe("forest");
    expect(lastSlideId(s)).toBe("forest");
  });

  it("a module with every slide unavailable falls back to the full list rather than nothing", () => {
    const s = slides(["a", false], ["b", false]);
    expect(firstSlideId(s)).toBe("a");
    expect(lastSlideId(s)).toBe("b");
  });
});

describe("cross-module wrap", () => {
  it("steps to the next module in nav order, and wraps from the last back to the first", () => {
    expect(nextModule("meic")).toBe("flies");
    expect(nextModule(MODULE_ORDER[MODULE_ORDER.length - 1]!)).toBe(MODULE_ORDER[0]);
  });

  it("steps to the previous module, and wraps from the first back to the last", () => {
    expect(prevModule("flies")).toBe("meic");
    expect(prevModule(MODULE_ORDER[0]!)).toBe(MODULE_ORDER[MODULE_ORDER.length - 1]);
  });
});
