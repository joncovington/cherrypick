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

  it("names every place the worst case is reached, not just the tail", () => {
    // 2026-09-24 live control: -$224.11 at the 7695 point, where the stranded 7685/7690 call spread
    // is fully lost and no fly pays, and again from 7710 up. The sentence used to say only "at or
    // above 7710", which read as though nothing near the settlement could reach it.
    const text = floorSentence(
      curve({
        worst: -224.11,
        worstAt: 7710,
        worstTail: "above",
        worstPlaces: [
          { lo: 7695, hi: 7695, tail: null },
          { lo: 7710, hi: 7760, tail: "above" },
        ],
      }),
    );
    expect(text).toContain("worst case -$224.11 at 7695 and at or above 7710");
  });

  it("names an interior flat run as a range", () => {
    const text = floorSentence(curve({ worstPlaces: [{ lo: 7690, hi: 7695, tail: null }] }));
    expect(text).toContain("from 7690 to 7695");
  });

  it("keeps the single-window sentence when there is only one", () => {
    const text = floorSentence(curve({ bands: [[7703, 7707]] }));
    expect(text).toContain("profitable between 7703 and 7707");
  });
});
