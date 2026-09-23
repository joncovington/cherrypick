import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { RegimeCrossCell, RegimeCrossTab } from "@console/shared";
import { CrossTabGrid } from "../src/components/RegimeCutsTab";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

function cell(buckets: string[], over: Partial<RegimeCrossCell> = {}): RegimeCrossCell {
  return {
    buckets,
    sessions: 8,
    trades: 20,
    netPnl: 100,
    avgPnl: 5,
    winRate: 0.6,
    completed: 16,
    completionRate: 0.8,
    thin: false,
    ...over,
  };
}

const tab = (cells: RegimeCrossCell[], dims = ["gex", "drift_alignment"]): RegimeCrossTab => ({
  dims,
  arms: [{ arm: "control", cells }],
});

describe("CrossTabGrid", () => {
  it("scales the cell colour with the completion rate and prints sessions", () => {
    const html = text(
      <CrossTabGrid
        tab={tab([cell(["diffuse", "with"]), cell(["diffuse", "against"], { completionRate: 0.2 })])}
        thinBelowSessions={3}
      />,
    );
    expect(html).toContain("rgba(67, 181, 122, 0.83"); // 0.15 + 0.85 * 0.8
    expect(html).toContain("rgba(67, 181, 122, 0.32"); // 0.15 + 0.85 * 0.2
    expect(html).toContain("80%");
    expect(html).toContain("8s");
  });

  it("greys a thin cell instead of colouring it, however high its rate", () => {
    const html = text(
      <CrossTabGrid tab={tab([cell(["diffuse", "with"], { thin: true, completionRate: 0.9 })])} thinBelowSessions={3} />,
    );
    expect(html).toContain("var(--row-line)");
    expect(html).toContain(">thin<");
    expect(html).not.toContain("rgba(67");
  });

  it("lays the first dimension down the rows and the second across the columns", () => {
    const html = text(
      <CrossTabGrid tab={tab([cell(["a", "x"]), cell(["b", "y"], { trades: 5 })])} thinBelowSessions={3} />,
    );
    for (const bucket of ["a", "b", "x", "y"]) expect(html).toContain(`>${bucket}<`);
    expect(html).toContain("repeat(2,");
    expect(html).toContain("20 trades");
  });

  it("falls back to win rate for a module with no completion concept, and says so", () => {
    const html = text(
      <CrossTabGrid
        tab={tab([cell(["diffuse", "up_from_open"], { completed: null, completionRate: null, winRate: 0.5 })], [
          "gex",
          "trend",
        ])}
        thinBelowSessions={3}
      />,
    );
    expect(html).toContain("rgba(67, 181, 122, 0.575"); // 0.15 + 0.85 * 0.5
    expect(html).toContain("Colour is win rate");
  });
});
