import { describe, it, expect } from "vitest";
import type { GexStrikeRow } from "../src/analytics/gex.js";
import {
  COVERAGE_EDGE_STRIKES,
  STRADDLE_BAND,
  STRADDLE_PER_SIGMA,
  WALLS_PER_SIDE,
  atCoverageEdge,
  atSpot,
  expectedMove,
  magnet,
  maxPain,
  oiCoverage,
  panelWalls,
  putCallRatios,
  straddleMove,
} from "../src/analytics/gexPanel.js";

/** The experimental GEX panel's levels: display only, each with the definition its module states. */

function row(strike: number, o: Partial<GexStrikeRow> = {}): GexStrikeRow {
  return { strike, net_gex: 0, call_gex: 0, put_gex: 0, call_oi: 0, put_oi: 0, call_vol: 0, put_vol: 0, ...o } as GexStrikeRow;
}

describe("panel walls", () => {
  // Put-heavy at every strike, so no strike's NET is positive: the net ranking found no call wall here.
  const series = [
    row(7600, { put_gex: -300, put_oi: 100, put_vol: 50 }),
    row(7650, { put_gex: -500, put_oi: 200, put_vol: 2000 }),
    row(7700, { call_gex: 900, put_gex: -1000 }), // below spot: no call wall, however large
    row(7750, { call_gex: 200, put_gex: -400 }), // above spot: no put wall
    row(7800, { call_gex: 600, put_gex: -700, call_oi: 300, call_vol: 30 }),
    row(7850, { call_gex: 800, put_gex: -900, call_vol: 10 }),
    row(7900, { call_gex: 100, put_gex: -150 }),
  ];

  it("ranks call walls by call gamma above spot and put walls by put gamma below it", () => {
    const w = panelWalls(series, 7720);
    expect(w.call.map((x) => x.strike)).toEqual([7850, 7800, 7750]);
    expect(w.put.map((x) => x.strike)).toEqual([7700, 7650, 7600]);
    expect(w.call.length).toBeLessThanOrEqual(WALLS_PER_SIDE);
  });

  it("shares each wall of its side's chain-wide gamma, with its own side's vol/OI", () => {
    const w = panelWalls(series, 7720);
    expect(w.call[0]!.share).toBeCloseTo(800 / 2600); // 900+200+600+800+100
    expect(w.put[1]!.share).toBeCloseTo(500 / 3950);
    expect(w.put[1]!.volOi).toBe(10);
    expect(w.call[0]!.volOi).toBeNull(); // no OI: no ratio, never infinity
  });
});

describe("max pain", () => {
  it("picks the strike that pays the chain's open interest least", () => {
    const s = [row(90, { put_oi: 100 }), row(100, { call_oi: 10, put_oi: 10 }), row(110, { call_oi: 100 })];
    // settling at 90 pays the 100 put 10 x 10; at 110 the 100 call 10 x 10; at 100 nothing
    expect(maxPain(s)).toBe(100);
  });

  it("is null with no open interest", () => {
    expect(maxPain([row(100, { net_gex: 5 })])).toBeNull();
  });
});

describe("expected move", () => {
  it("scales the IV by the time left to expiry, and is null once it has passed", () => {
    const twoHours = 2 * 3600;
    expect(expectedMove(100, 0.5, twoHours)).toBeCloseTo(100 * 0.5 * Math.sqrt(twoHours / (365 * 86_400)));
    expect(expectedMove(100, 0.5, 0)).toBeNull();
    expect(expectedMove(100, 0.5, -60)).toBeNull();
  });

  it("reads 1 sigma off the straddle as straddle / sqrt(2/pi), and the 0.85 band as about 0.68 sigma", () => {
    expect(straddleMove(STRADDLE_PER_SIGMA * 10)).toBeCloseTo(10);
    expect((STRADDLE_BAND * STRADDLE_PER_SIGMA * 10) / straddleMove(STRADDLE_PER_SIGMA * 10)).toBeCloseTo(0.678, 3);
  });

  it("agrees with the IV move when the straddle is priced off the same IV", () => {
    // ATM straddle ~ sqrt(2/pi) x S x sigma x sqrt(T) for a short horizon
    const s = 7500, iv = 0.15, secs = 3 * 3600;
    const straddle = STRADDLE_PER_SIGMA * s * iv * Math.sqrt(secs / (365 * 86_400));
    expect(straddleMove(straddle)).toBeCloseTo(expectedMove(s, iv, secs)!, 6);
  });

  it("interpolates a per-strike value to spot between the strikes either side", () => {
    const pts = [{ strike: 100, value: 4 }, { strike: 105, value: 2 }, { strike: 110, value: 6 }];
    expect(atSpot(pts, 101)).toBeCloseTo(3.6);
    expect(atSpot(pts, 105)).toBe(2);
    expect(atSpot(pts, 95)).toBe(4); // outside the chain: the nearest strike's
    expect(atSpot([], 100)).toBeNull();
  });
});

describe("magnet", () => {
  it("names the most open interest inside the band, its share and its gamma's sign", () => {
    const s = [
      row(95, { call_oi: 100, put_oi: 200, net_gex: -5 }),
      row(100, { call_oi: 50, put_oi: 50, net_gex: 9 }),
      row(120, { call_oi: 9000, net_gex: 1 }),
    ];
    expect(magnet(s, 94, 106)).toEqual({ strike: 95, share: 0.75, oi: 300, gammaSign: -1 });
    expect(magnet(s, 101, 104)).toBeNull();
  });
});

describe("put/call ratios", () => {
  it("is put over call for OI and volume, null with no calls", () => {
    const r = [row(1, { call_oi: 4, put_oi: 2, call_vol: 1, put_vol: 3 })];
    expect(putCallRatios(r, r)).toEqual({ oi: 0.5, volume: 3 });
    expect(putCallRatios([row(1, { put_oi: 2 })], [row(1)])).toEqual({ oi: null, volume: null });
  });

  it("takes OI over the covered strikes and volume over the live window", () => {
    const covered = [row(1, { call_oi: 4, put_oi: 2 }), row(2, { call_oi: 0, put_oi: 6 })];
    const window = [row(1, { call_vol: 2, put_vol: 1 })];
    expect(putCallRatios(covered, window)).toEqual({ oi: 2, volume: 0.5 });
  });
});

describe("walls by volume", () => {
  // OI says 7800, the session's volume says 7850: the basis decides the wall.
  const series = [
    row(7700, { put_gex: -100, put_gex_vol: -900, put_oi: 10, put_vol: 900 }),
    row(7650, { put_gex: -800, put_gex_vol: -50, put_oi: 800, put_vol: 50 }),
    row(7800, { call_gex: 600, call_gex_vol: 10, call_oi: 600, call_vol: 10 }),
    row(7850, { call_gex: 50, call_gex_vol: 700, call_oi: 5, call_vol: 700 }),
  ];

  it("ranks on the basis asked for, and shares each wall of that basis's total", () => {
    expect(panelWalls(series, 7720).call[0]!.strike).toBe(7800);
    expect(panelWalls(series, 7720, "volume").call[0]!.strike).toBe(7850);
    expect(panelWalls(series, 7720).put[0]!.strike).toBe(7650);
    const v = panelWalls(series, 7720, "volume");
    expect(v.put[0]!.strike).toBe(7700);
    expect(v.put[0]!.gex).toBe(-900);
    expect(v.call[0]!.share).toBeCloseTo(700 / 710);
  });
});

describe("open interest coverage", () => {
  const rows = [90, 95, 100, 105, 110, 115, 120].map((k) => row(k, { call_oi: 1 }));

  it("spans the strikes holding open interest", () => {
    expect(oiCoverage([...rows, row(200)])).toEqual({ low: 90, high: 120, strikes: 7 });
    expect(oiCoverage([row(1)])).toBeNull();
  });

  it("flags max pain within two held strikes of either end, not inside", () => {
    expect(COVERAGE_EDGE_STRIKES).toBe(2);
    expect(atCoverageEdge(rows, 90)).toBe(true);
    expect(atCoverageEdge(rows, 95)).toBe(true);
    expect(atCoverageEdge(rows, 100)).toBe(false);
    expect(atCoverageEdge(rows, 110)).toBe(false);
    expect(atCoverageEdge(rows, 115)).toBe(true);
    expect(atCoverageEdge(rows, 120)).toBe(true);
  });
});
