import { describe, it, expect } from "vitest";
import { gexStrikeWindow, type GexStrikeRow } from "../src/pages/Gex/GexProfileChart";

function row(strike: number, gex: number): GexStrikeRow {
  return {
    strike, call_iv: 0, put_iv: 0, call_oi: 0, put_oi: 0, call_vol: 0, put_vol: 0, total_vol: 0,
    call_gex: 0, put_gex: 0, net_gex: gex, abs_gex: Math.abs(gex), call_gex_vol: 0, put_gex_vol: 0, net_gex_vol: 0,
  };
}
const mag = (r: GexStrikeRow) => Math.abs(r.net_gex);
// SPX-like chain every 5 points 7500..8000; real GEX near 7740..7760, a dust of it everywhere else.
const chain = Array.from({ length: 101 }, (_, i) => 7500 + i * 5).map((k) =>
  row(k, k >= 7740 && k <= 7760 ? 1e9 : 1e6),
);
const strikes = (rs: GexStrikeRow[]) => rs.map((r) => r.strike);

describe("which strikes the GEX chart draws", () => {
  it("auto fits the meaningful strikes and spot, padded three strikes, ignoring far-out dust", () => {
    const s = strikes(gexStrikeWindow(chain, 7744, "auto", mag));
    expect(Math.max(...s)).toBe(7775);
    expect(Math.min(...s)).toBe(7725);
  });

  it("auto widens to keep today's spot trail on the chart", () => {
    const s = strikes(gexStrikeWindow(chain, 7744, "auto", mag, [7675, 7790]));
    expect(Math.min(...s)).toBe(7660);
    expect(Math.max(...s)).toBe(7805);
  });

  it("a band is ± that percent of spot, so it scales with the symbol", () => {
    const spx = strikes(gexStrikeWindow(chain, 7744, "1", mag));
    expect(Math.min(...spx)).toBeGreaterThanOrEqual(7744 - 77.44);
    expect(Math.max(...spx)).toBeLessThanOrEqual(7744 + 77.44);
    const xspChain = Array.from({ length: 101 }, (_, i) => 750 + i * 0.5).map((k) => row(k, 1e8));
    const xsp = strikes(gexStrikeWindow(xspChain, 774.4, "1", mag));
    expect(Math.min(...xsp)).toBeGreaterThanOrEqual(774.4 - 7.744);
    expect(Math.max(...xsp)).toBeLessThanOrEqual(774.4 + 7.744);
  });

  it("returns strikes high to low, the chart's top-down order", () => {
    const s = strikes(gexStrikeWindow(chain, 7744, "auto", mag));
    expect(s).toEqual([...s].sort((a, b) => b - a));
  });
});
