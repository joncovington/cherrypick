import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ModuleSummarySlice } from "@console/shared";
import { ExperimentsPage, lastTradedOf, shownExperiments } from "../src/pages/Experiments/ExperimentsPage";

describe("an experiment switched off", () => {
  const on = (pmcc: boolean) =>
    ({
      ok: true,
      capabilities: {},
      modules: Object.fromEntries(["pmcc", "calendars", "curve", "contango", "weeklyput"].map((m) => [m, { configured: true, enabled: m === "pmcc" ? pmcc : true, missing: [] }])),
      services: {},
      features: {},
    }) as unknown as Parameters<typeof shownExperiments>[0];

  it("loses its card with its pages, and an unread answer shows every card", () => {
    expect(shownExperiments(on(false)).map((e) => e.id)).toEqual(["calendars", "curve", "contango", "weeklyput"]);
    expect(shownExperiments(on(true)).map((e) => e.id)).toEqual(["pmcc", "calendars", "curve", "contango", "weeklyput"]);
    expect(shownExperiments(undefined)).toHaveLength(5);
  });
});

/**
 * Batch 4 of docs/console-review-plan.md: the paper-only experiments and the retired flies on
 * one page, a card each, every figure the module's own summary read.
 */

const render = () =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");

describe("the Experiments page", () => {
  it("has a card for each experiment, with its tested status and links into its own pages", () => {
    const html = render();
    for (const id of ["pmcc", "calendars", "curve", "contango"]) {
      expect(html).toContain(`data-experiment="${id}"`);
      expect(html).toContain(`href="/${id}/positions"`);
    }
    expect(html).toContain("drawdown, not return"); // contango's status line
  });

  it("keeps flies as a record, in the repo's own words, never inventing why", () => {
    const html = render();
    expect(html).toContain('data-experiment="flies"');
    expect(html).toContain("Retired by the developer in the live config on 2026-10-19; may be reinstated at a later date");
  });

  it("names the last traded day only when it is not today", () => {
    const day = (from: string) => ({ window: { label: "day", from, to: from } }) as unknown as ModuleSummarySlice;
    expect(lastTradedOf(day("2026-10-19"), "2026-10-30")).toBe("2026-10-19");
    expect(lastTradedOf(day("2026-10-30"), "2026-10-30")).toBeNull();
    expect(lastTradedOf(undefined, "2026-10-30")).toBeNull();
  });
});
