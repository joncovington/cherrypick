import { describe, it, expect } from "vitest";
import { netWalls, type GexStrikeRow } from "../src/analytics/gex.js";

/** Mirrors core's tests/test_gex_one_sided_walls.py: a wall needs a strike on its own side of zero. */

function row(strike: number, net: number): GexStrikeRow {
  return { strike, net_gex: net, net_gex_vol: net } as GexStrikeRow;
}

describe("one-sided walls", () => {
  it("names no put wall on an expired chain with no negative strike", () => {
    const chain = [row(3000, 0), row(7805, 2455e6), row(7810, 41904e6), row(7815, 5604e6), row(9000, 0)];
    expect(netWalls(chain, "net_gex")).toEqual([7810, null]);
  });

  it("names no call wall with no positive strike", () => {
    expect(netWalls([row(7700, -5), row(7740, -10), row(7800, 0)], "net_gex")).toEqual([null, 7740]);
  });

  it("leaves a two-sided chain unchanged", () => {
    expect(netWalls([row(7730, -300), row(7765, -10), row(7800, 6000)], "net_gex")).toEqual([7800, 7730]);
  });
});
