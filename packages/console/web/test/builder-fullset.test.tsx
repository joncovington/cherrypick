import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderSuggestion } from "@console/shared";
import { AddToFullSet } from "../src/pages/Builder/BuilderPage";

const html = (el: React.ReactElement) =>
  renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const SHORT: BuilderSuggestion = {
  blocks: ["structure"],
  window: ["2025-11-17", "2026-11-16"],
  needs: [{ field: "chain", expiry: "0dte", cadence: "1m", covered: false, source: null, licensed: false }],
  possible: false,
  licensed: false,
  full_set: false,
};

/** The offer to add an underlying to the full set (#1253): only where our data falls short for one
 *  outside the set, and only as an offer to plan first; nothing is fetched from this line alone. */
describe("adding an underlying to the full set from the Builder", () => {
  it("offers to plan the addition for an underlying outside the set that our data does not cover", () => {
    const out = html(<AddToFullSet name="mine" symbol="abc" s={SHORT} />);
    expect(out).toContain("ABC is not one of the full set");
    expect(out).toContain("Add ABC to the full set");
    expect(out).toContain('data-fullset="offer"');
  });

  it("offers nothing for one of the set's own, a covered backtest, or an older module that does not say", () => {
    expect(html(<AddToFullSet name="mine" symbol="SPY" s={{ ...SHORT, full_set: true }} />)).toBe("");
    expect(html(<AddToFullSet name="mine" symbol="ABC" s={{ ...SHORT, possible: true }} />)).toBe("");
    const { full_set: _, ...older } = SHORT;
    expect(html(<AddToFullSet name="mine" symbol="ABC" s={older} />)).toBe("");
  });
});
