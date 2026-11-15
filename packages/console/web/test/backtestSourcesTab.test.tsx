import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { SourceRow, SourcesSummary } from "@console/shared";
import { byKind, coverageQuery, SourcesBody, spanLabel } from "../src/pages/Backtest/SourcesTab";
import { slides } from "../src/lightbox/manifests/BacktestLightbox";
import { BACKTEST_SLIDES } from "../src/lightbox/navGroups";
import { FAIL_CLOSED_SLIDES } from "../src/lib/visibility";

/**
 * Backtest → sources (#1194): the source registry as core reports it. The page groups and labels the
 * rows; it recomputes no span and judges no coverage.
 */

const row = (over: Partial<SourceRow>): SourceRow => ({
  name: "stock-history",
  holds: "US stocks and ETFs, daily bars",
  kind: "bars",
  fields: ["closes", "ohlc"],
  cadence: "1d",
  expiries: [],
  licensed: false,
  writer: "technicals history landing",
  ongoing: true,
  rolling: false,
  note: null,
  state: "read",
  reason: null,
  symbols: 21917,
  first: "2011-01-03",
  last: "2026-11-13",
  read_at: "2026-11-15T09:00:00",
  ...over,
});

const DOC: SourcesSummary = {
  generated_at: "2026-11-15T09:00:00",
  undeclared: [],
  withheld: 0,
  sources: [
    row({ name: "fred-rates", kind: "rates", holds: "the 3-month bill rate", symbols: 1 }),
    row({}),
    row({
      name: "dolt-chains",
      kind: "chain",
      holds: "end-of-day chains",
      expiries: ["monthly"],
      note: "early years are sparse",
      symbols: 2339,
      first: "2019-02-09",
    }),
    row({ name: "gex-profile", kind: "profile", state: "absent", reason: "no gex/gex_history.db on this machine", symbols: null, first: null, last: null }),
    row({ name: "vendor-oi", kind: "chain", licensed: true, symbols: null }),
  ],
};

describe("Backtest → sources", () => {
  it("is a tab on the page that never fails closed", () => {
    expect(BACKTEST_SLIDES.map((s) => s.id)).toContain("sources");
    expect(slides.map((s) => s.id)).toEqual(BACKTEST_SLIDES.map((s) => s.id));
    expect([...(FAIL_CLOSED_SLIDES.get("backtest") ?? new Set<string>())]).not.toContain("sources");
  });

  it("groups by kind, prices first and rates last", () => {
    expect(byKind(DOC.sources).map(([k]) => k)).toEqual(["bars", "chain", "profile", "rates"]);
  });

  it("shows each source's span, state, expiries and licence as core reports them", () => {
    const html = renderToString(<SourcesBody doc={DOC} />).replace(/<!-- -->/g, "");
    expect(html).toContain("5 sources, 4 read by the inventory of 2026-11-15T09:00:00");
    expect(html).toContain("2011-01-03 to 2026-11-13");
    expect(html).toContain("expiries: monthly");
    expect(html).toContain("early years are sparse");
    expect(html).toContain("no gex/gex_history.db on this machine");
    expect(html).toContain("licensed");
    expect(html).toContain("21,917");
    expect(html).not.toContain("Data files no source declares");
  });

  it("names undeclared files and the vendor rows held back", () => {
    const html = renderToString(<SourcesBody doc={{ ...DOC, undeclared: ["market-files/new.db"], withheld: 8, generated_at: null }} />).replace(/<!-- -->/g, "");
    expect(html).toContain("Data files no source declares: market-files/new.db");
    expect(html).toContain("8 of the vendor&#x27;s sources are not listed");
    expect(html).toContain("no inventory has run yet");
  });

  it("builds the coverage query from the form, leaving out what was not chosen", () => {
    expect(coverageQuery({ field: "chain", symbol: " /es ", from: "2026-11-05", to: "2026-11-13", cadence: "", expiry: "0dte" })).toBe(
      "field=chain&symbol=%2FES&from=2026-11-05&to=2026-11-13&expiry=0dte",
    );
    expect(spanLabel({ first: null, last: null })).toBe("—");
    expect(spanLabel({ first: "2026-11-13", last: "2026-11-13" })).toBe("2026-11-13");
  });
});
