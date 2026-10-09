import { describe, it, expect } from "vitest";
import { fmtWall } from "../src/lightbox/manifests/GexLightbox";

/** A near-tied wall reads as both strikes, a clear one as one (server `contestedWalls`). */
describe("fmtWall", () => {
  it("shows the runner-up beside a contested wall", () => {
    expect(fmtWall(7740, { strike: 7600 })).toBe("7740 / 7600");
  });

  it("shows the wall alone when it is clear or the payload predates alternates", () => {
    expect(fmtWall(7740, null)).toBe("7740");
    expect(fmtWall(7740, undefined)).toBe("7740");
  });

  it("shows a dash with no wall", () => {
    expect(fmtWall(null, { strike: 7600 })).toBe("—");
  });
});
