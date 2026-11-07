import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QuoteHistoryCoverage } from "@console/shared";
import { RiskTab } from "../src/pages/Compare/RiskTab";
import { CalendarTab } from "../src/pages/Compare/CalendarTab";
import { ExperimentsPage } from "../src/pages/Experiments/ExperimentsPage";
import { DataEvidenceCards } from "../src/pages/System/DataEvidenceCards";

/**
 * The suite pages outside the modules took the portfolio tab's layout (2026-11-07): their cards sit
 * under a Section question inside a `pf-layout`, the old `cards` grid is gone, long secondary tables
 * open from their card, and local selects sit behind filter & sort.
 */

const coverage: QuoteHistoryCoverage = {
  ok: true,
  db: "x.db",
  exists: true,
  recorded_from: "2026-10-13",
  since: "2026-10-13",
  until: "2026-10-13",
  gap_seconds: 60,
  symbols: {
    XYZ: {
      recorded: 1,
      missing: [],
      sessions: [
        { session: "2026-10-13", recorded: true, samples: 1234, first: "09:30:05", last: "16:00:00", gaps: [], max_gap_s: 30, no_spot: 0, no_spot_reasons: {}, strikes_median: 80 },
      ],
    },
  },
};

function render(path: string, el: React.ReactElement, data: Array<[unknown[], unknown]> = []): string {
  const client = new QueryClient();
  for (const [k, v] of data) client.setQueryData(k, v);
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>{el}</QueryClientProvider>
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

describe("the suite pages' layout", () => {
  it.each([
    ["compare risk", <RiskTab key="r" />],
    ["compare calendar", <CalendarTab key="c" />],
    ["experiments", <ExperimentsPage key="e" />],
  ])("%s sits in sections, not the old cards grid", (_name, el) => {
    const html = render("/", el);
    expect(html).toContain("pf-layout");
    expect(html).toContain("pf-sec");
    expect(html).not.toContain("cards-wide");
  });

  it("keeps the quote-history session table behind its card's button until it is opened", () => {
    const data: Array<[unknown[], unknown]> = [[["bridged", "quote-history"], { ok: true, data: coverage, error: null }]];
    const closed = render("/", <DataEvidenceCards />, data);
    expect(closed).toContain("session table");
    expect(closed).not.toContain("longest gap");
  });
});
