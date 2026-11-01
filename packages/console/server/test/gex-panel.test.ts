import { describe, it, expect } from "vitest";
import type { GexStrikeRow } from "../src/analytics/gex.js";
import { expectedMove, magnet, maxPain, panelWalls, putCallRatios, WALLS_PER_SIDE } from "../src/analytics/gexPanel.js";

/** The experimental GEX panel's levels: display only, each with the definition its module states. */

function row(strike: number, net: number, o: Partial<GexStrikeRow> = {}): GexStrikeRow {
  return { strike, net_gex: net, call_oi: 0, put_oi: 0, call_vol: 0, put_vol: 0, ...o } as GexStrikeRow;
}

describe("panel walls", () => {
  const series = [
    row(7600, -300, { put_oi: 100, put_vol: 50 }),
    row(7650, -500, { put_oi: 200, put_vol: 2000 }),
    row(7700, 400), // above-spot-only rule: a positive strike below spot is no call wall
    row(7750, -100),
    row(7800, 600, { call_oi: 300, call_vol: 30 }),
    row(7850, 900, { call_oi: 0, call_vol: 10 }),
    row(7900, -50), // negative above spot is no call wall
    row(7950, 100),
    row(8000, 200),
  ];

  it("lists call walls above spot and put walls below it, strongest first", () => {
    const w = panelWalls(series, 7720);
    expect(w.call.map((x) => x.strike)).toEqual([7850, 7800, 8000]);
    expect(w.put.map((x) => x.strike)).toEqual([7650, 7600]);
    expect(w.call.length).toBeLessThanOrEqual(WALLS_PER_SIDE);
  });

  it("shares each wall of its sign's chain-wide total, with its own side's vol/OI", () => {
    const w = panelWalls(series, 7720);
    expect(w.call[0]!.share).toBeCloseTo(900 / 2200); // 400+600+900+100+200
    expect(w.put[0]!.share).toBeCloseTo(500 / 950); // 300+500+100+50
    expect(w.put[0]!.volOi).toBe(10);
    expect(w.call[0]!.volOi).toBeNull(); // no OI: no ratio, never infinity
  });
});

describe("max pain", () => {
  it("picks the strike that pays the chain's open interest least", () => {
    const s = [row(90, 0, { put_oi: 100 }), row(100, 0, { call_oi: 10, put_oi: 10 }), row(110, 0, { call_oi: 100 })];
    // at 90: calls 10*0 + 100*0, puts 10*10 = 100; at 100: puts 0, calls 0 = 0; at 110: calls 10*10 = 100
    expect(maxPain(s)).toBe(100);
  });

  it("is null with no open interest", () => {
    expect(maxPain([row(100, 5)])).toBeNull();
  });
});

describe("magnet and expected move", () => {
  it("names the largest |net| inside the band and its share of the band", () => {
    const s = [row(95, -300), row(100, 100), row(105, -200), row(120, 9000)];
    expect(magnet(s, 94, 106)).toEqual({ strike: 95, share: 0.5 });
    expect(magnet(s, 101, 104)).toBeNull();
  });

  it("scales the IV by the time left to expiry, and is null once it has passed", () => {
    const twoHours = 2 * 3600;
    expect(expectedMove(100, 0.5, twoHours)).toBeCloseTo(100 * 0.5 * Math.sqrt(twoHours / (365 * 86_400)));
    expect(expectedMove(100, 0.5, 0)).toBeNull();
    expect(expectedMove(100, 0.5, -60)).toBeNull();
  });
});

describe("put/call ratios", () => {
  it("is put over call for OI and volume, null with no calls", () => {
    expect(putCallRatios([row(1, 0, { call_oi: 4, put_oi: 2, call_vol: 1, put_vol: 3 })])).toEqual({ oi: 0.5, volume: 3 });
    expect(putCallRatios([row(1, 0, { put_oi: 2 })])).toEqual({ oi: null, volume: null });
  });
});
