import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationReading } from "@console/shared";
import { at, bandChart, stripDomain, WorkingBody } from "../src/pages/Automations/AutomationWorking";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const RUN = "builder-mine-2611201000";
const BASE: AutomationReading = { name: "mine", pinned: RUN, withheld: false, error: null, reading: null };
const BAND = Array.from({ length: 25 }, (_, i) => ({ k: i + 1, p10: -40 * Math.sqrt(i + 1), p50: 5 * (i + 1), p90: 40 * Math.sqrt(i + 1) }));

const FULL: AutomationReading = {
  ...BASE,
  reading: {
    state: "inside",
    licensed: false,
    reference: {
      run: RUN,
      automation: "mine",
      symbol: "SPX",
      first: "2025-11-01",
      last: "2026-11-01",
      trades: 250,
      sessions: 250,
      insufficient: false,
      mean: 5,
      percentiles: { p10: -40, p25: -10, p50: 6, p75: 20, p90: 39 },
      entry_rate: 0.8,
      band: BAND,
      sample_computed: 120,
    },
    paper: {
      trades: 5,
      rows: [
        { i: 1, session: "2026-11-16", net: -60, place: "below", percentile: 3.5, running: -60 },
        { i: 2, session: "2026-11-17", net: 10, place: "inside", percentile: 58, running: -50 },
        { i: 3, session: "2026-11-18", net: 12, place: "inside", percentile: 61, running: -38 },
        { i: 4, session: "2026-11-19", net: 45, place: "above", percentile: 93, running: 7 },
        { i: 5, session: "2026-11-20", net: 8, place: "inside", percentile: 55, running: 15 },
      ],
      counts: { below: 1, inside: 3, above: 1 },
      net: 15,
      mean: 3,
      running_place: "inside",
      band_at: BAND[4]!,
      entry: { entered: 5, missed: 1, rate: 0.8333 },
    },
    sample: { needed: 40, basis: "declared", have: 5 },
  },
};

describe("Is it working?", () => {
  it("asks for a pin when none is set, with the way to the Builder", () => {
    const out = html(<WorkingBody name="mine" data={{ ...BASE, pinned: null }} />);
    expect(out).toContain("No Builder run is pinned as this automation&#x27;s backtest reference");
    expect(out).toContain('href="/automations/builder?automation=mine"');
  });

  it("says a licensed reference is withheld, and shows no figure", () => {
    const out = html(<WorkingBody name="mine" data={{ ...BASE, withheld: true }} />);
    expect(out).toContain("is licensed, and licensed results are not shown now");
    expect(out).not.toContain("$");
  });

  it("says a pinned run too short for a reference is too short", () => {
    const data: AutomationReading = {
      ...BASE,
      reading: { state: "no_reference", licensed: false, reference: { run: RUN, automation: "mine", symbol: "SPX", first: null, last: null, trades: 12, sessions: 12, insufficient: true, min_trades: 30 }, paper: { trades: 0, entry: { entered: 0, missed: 0, rate: null } } },
    };
    expect(html(<WorkingBody name="mine" data={data} />)).toContain("has 12 trades; a reference needs 30");
  });

  it("reads the record: the state, the sample against the declared count, the rates, each trade's place", () => {
    const out = html(<WorkingBody name="mine" data={FULL} />);
    expect(out).toContain(">on track<");
    expect(out).toContain("3 of 5 trades inside the usual range, 1 below, 1 above");
    expect(out).toContain(">5 of 40<");
    expect(out).toContain("trades its forward rule declares");
    expect(out).toContain(">83% vs 80%<");
    expect(out).toContain('<span class="chip chip-warn">below</span>');
    expect(out).toContain('<span class="chip chip-info">above</span>');
    expect(out.match(/class="ar-strip-dot /g)?.length).toBe(5);
    expect(out).toContain("2025-11-01 to 2026-11-01");
  });

  it("says when the sample is computed rather than declared", () => {
    const reading = { ...FULL.reading!, sample: { needed: 120, basis: "computed" as const, have: 5 } };
    expect(html(<WorkingBody name="mine" data={{ ...FULL, reading }} />)).toContain("trades the run&#x27;s spread needs, computed");
  });

  it("scales without inventing: the strip widens to every trade, a point maps onto the width", () => {
    expect(stripDomain(-40, 39, [-60, 45])).toEqual([-79.5, 78.5]);
    expect(stripDomain(-40, 39, [-200])).toEqual([-200, 78.5]);
    expect(at(0, -10, 10)).toBe(50);
    expect(at(99, -10, 10)).toBe(100);
    const c = bandChart(BAND, [-60, -50]);
    expect(c.K).toBe(25);
    expect(c.lo).toBe(Math.min(-60, ...BAND.map((b) => b.p10)));
    expect(c.today).not.toBeNull();
  });
});
