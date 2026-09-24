import { describe, it, expect } from "vitest";
import { floorSentence } from "../src/pages/Flies/ForestCard";

type Curve = Parameters<typeof floorSentence>[0];

function curve(floor: Partial<Curve["floor"]>): Curve {
  return {
    empty: false, positions: 5, prices: [], pnl: [], structures: [], centers: [], wing: 5,
    floor: {
      worst: -201, worstAt: 7735, worstTail: "above", floorHolds: false, locked: false,
      band: [7703, 7707], bandOpen: { below: false, above: false },
      bands: [], bandsOpen: { below: false, above: false }, unboundedBelow: true,
      ...floor,
    },
  };
}

describe("the forest's floor sentence", () => {
  it("names every profitable window when there is more than one", () => {
    // 2026-09-23 after the cancelled entries were dropped.
    const text = floorSentence(
      curve({
        bands: [[7645, 7697], [7703, 7707], [7713, 7722], [7728, 7732]],
        bandsOpen: { below: true, above: false },
      }),
    );

    expect(text).toContain("profitable only in 4 separate windows");
    expect(text).toContain("below 7697, 7703–7707, 7713–7722, 7728–7732");
    expect(text).toContain("loses between them and above 7732");
    // The old caption: one window named, the other three called losses.
    expect(text).not.toContain("outside that band");
    // A scan edge is not an edge of the book.
    expect(text).not.toContain("7645");
  });

  it("keeps the single-window sentence when there is only one", () => {
    const text = floorSentence(curve({ bands: [[7703, 7707]] }));
    expect(text).toContain("profitable between 7703 and 7707");
  });
});
