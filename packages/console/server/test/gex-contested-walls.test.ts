import { describe, it, expect } from "vitest";
import { contestedWalls, netWalls, WALL_NEAR_TIE, type GexStrikeRow } from "../src/analytics/gex.js";

/**
 * A near-tied wall is shown with its runner-up (2026-10-09): over 229 recorded SPX snapshots, 52 of
 * 53 put-wall hops were between near-tied strikes, 7740 and 7600 swapping all of 2026-10-06. The
 * wall itself must not change -- it is the recorder's and the wall-clear arm's.
 */

function row(strike: number, net: number, netVol = 0): GexStrikeRow {
  return { strike, net_gex: net, net_gex_vol: netVol } as GexStrikeRow;
}

const SERIES = [row(7600, -900), row(7700, 100), row(7740, -1000), row(7800, 5000), row(7810, 3000)];

describe("contested walls", () => {
  it("names the runner-up when it is within the near-tie of the wall", () => {
    const got = contestedWalls(SERIES, "net_gex");
    expect(got.put).toEqual({ strike: 7600, strength: 0.9 });
    expect(netWalls(SERIES, "net_gex")).toEqual([7800, 7740]); // the wall is unchanged
  });

  it("says nothing when the wall is clear", () => {
    expect(contestedWalls(SERIES, "net_gex").call).toBeNull(); // 3000 / 5000 = 0.6
  });

  it("uses the near-tie as a strict bound", () => {
    const at = [row(7600, -800), row(7740, -1000), row(7800, 10)];
    expect(-800 / -1000).toBe(WALL_NEAR_TIE);
    expect(contestedWalls(at, "net_gex").put).toBeNull();
  });

  it("never offers a runner-up from the other side of zero", () => {
    const lone = [row(7740, -1000), row(7800, 900)];
    const got = contestedWalls(lone, "net_gex");
    expect(got.put).toBeNull();
    expect(got.call).toBeNull();
  });

  it("offers no call runner-up on an all-negative chain, nor a put one on an all-positive chain", () => {
    // netWalls still names a "call wall" here (the least negative strike); it has no runner-up to show.
    expect(contestedWalls([row(7700, -95), row(7740, -100)], "net_gex").call).toBeNull();
    expect(contestedWalls([row(7700, 95), row(7740, 100)], "net_gex").put).toBeNull();
  });

  it("reads the basis it is asked for", () => {
    const flow = [row(7600, 0, -950), row(7740, 0, -1000), row(7800, 0, 10)];
    expect(contestedWalls(flow, "net_gex_vol").put).toEqual({ strike: 7600, strength: 0.95 });
    expect(contestedWalls(flow, "net_gex").put).toBeNull();
  });

  it("handles an empty series", () => {
    expect(contestedWalls([], "net_gex")).toEqual({ call: null, put: null });
  });
});
