import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MeicForestCard } from "../src/pages/Meic/MeicForestCard";

/**
 * MEIC's forest keeps the chart wide on a phone (2026-11-07): its x width, y scale, y range and
 * per-condor switch sit behind filter & sort with a summary line, as the flies forest's do, instead of
 * four selects stacked in the card's head above a squeezed chart.
 */
const html = () =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <MeicForestCard mode="paper" scope={{ day: null, symbol: null, profile: null, era: null }} />
      </MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

describe("the MEIC forest's controls", () => {
  it("sit behind filter & sort, with what is on said in the summary line", () => {
    const out = html();
    expect(out).toContain('aria-label="Filter and sort"');
    expect(out).not.toContain("<select");
    for (const part of ["x width auto", "book total", "y range auto", "each condor shown"]) expect(out).toContain(part);
  });
});
