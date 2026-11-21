import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ArmBacktestReading } from "@console/shared";
import { ArmBacktestBody } from "../src/components/armpage/ArmBacktest";

const html = (el: React.ReactElement) =>
  renderToString(<MemoryRouter>{el}</MemoryRouter>)
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const MATCH = { run: "r-design", run_arm: "n1", window: "design", first: "2022-05-16", last: "2025-06-30", sessions: 778 };
const BAND = Array.from({ length: 25 }, (_, i) => ({ k: i + 1, p10: -400 * Math.sqrt(i + 1), p50: 30 * (i + 1), p90: 400 * Math.sqrt(i + 1) }));
const BASE: ArmBacktestReading = { module: "meic", arm: "pm1400-n1", unit: "session", matches: [], chosen: null, paper_from: null, withheld: false, error: null, reading: null };

const FULL: ArmBacktestReading = {
  ...BASE,
  matches: [MATCH, { ...MATCH, run: "r-short", sessions: 300 }],
  chosen: MATCH,
  paper_from: "2026-11-03",
  reading: {
    state: "inside",
    licensed: true,
    reference: { run: "r-design", automation: "pm1400-n1", symbol: null, first: null, last: null, trades: 778, sessions: 778, insufficient: false, mean: 30, percentiles: { p10: -300, p25: -50, p50: 40, p75: 120, p90: 260 }, band: BAND },
    paper: {
      trades: 2,
      rows: [
        { i: 1, session: "2026-11-16", net: 100, place: "inside", percentile: 60, running: 100 },
        { i: 2, session: "2026-11-17", net: -50, place: "inside", percentile: 25, running: 50 },
      ],
      counts: { below: 0, inside: 2, above: 0 },
      net: 50,
      mean: 25,
      entry: { entered: 0, missed: 0, rate: null },
    },
    sample: { needed: 120, basis: "computed", have: 2 },
  },
};

describe("a module arm against its backtest", () => {
  it("says no stored run has tried its settings, with nothing standing in", () => {
    const out = html(<ArmBacktestBody data={BASE} />);
    expect(out).toContain("No stored run has tried pm1400-n1");
    expect(out).not.toContain("usual range");
  });

  it("names the licensed run it would read and withholds its figures", () => {
    const out = html(<ArmBacktestBody data={{ ...BASE, matches: [MATCH], chosen: MATCH, withheld: true }} />);
    expect(out).toContain("r-design (called n1 there, design window");
    expect(out).toContain("licensed results are not shown now");
  });

  it("reads each paper session on the run's range, in sessions, and names the run and the other matches", () => {
    const out = html(<ArmBacktestBody data={FULL} />);
    expect(out).toContain("on track");
    expect(out).toContain("2 of 2 sessions inside the usual range");
    expect(out).toContain("2 of 120");
    expect(out).toContain("25 sessions");
    expect(out).toContain("paper since 2026-11-03");
    expect(out).toContain("r-short (design)");
    expect(out).toContain(">n1<");
  });
});
