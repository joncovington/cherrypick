import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationReading, AutomationsLine } from "@console/shared";
import { ExitsCard, noExitsWhy, PremiumCard } from "../src/pages/Automations/AutomationDid";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const LINE: AutomationsLine = {
  trades: 9,
  net: 128,
  gross: 420,
  fees: 180,
  settlement: 12,
  slippage: 100,
  netStress: 60,
  netMid: 228,
  wins: 7,
  mean: 14.22,
  worst: -388,
  drawdown: 388,
  curve: [],
};

describe("what it did", () => {
  it("lays each exit beside the run's share, in the run's order, words not codes", () => {
    const out = html(
      <ExitsCard
        exits={[
          { kind: "settlement", trades: 6, share: 0.6667, net: 520, run_share: 0.69 },
          { kind: "stop", trades: 1, share: 0.1111, net: -388, run_share: 0.09 },
          { kind: "trailing_stop", trades: 0, share: 0, net: 0, run_share: 0.22 },
        ]}
      />,
    );
    expect(out).toContain("held to the posted close");
    expect(out).toContain("closed at its stop");
    expect(out).toContain("closed at its trailing stop");
    expect(out.indexOf("posted close")).toBeLessThan(out.indexOf("at its stop"));
    expect(out).toContain("67%");
    expect(out).toContain("69%");
    expect(out).toContain("7 trades");
  });

  it("shows the premium as gross, each cost its own row, then net, from the line as served", () => {
    const out = html(<PremiumCard line={LINE} />);
    const text = out.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("gross +$420.00 fees -$180.00 settlement -$12.00 slippage -$100.00 the model net P&amp;L $128.00 · $14.22 a trade");
  });

  it("says why there is no exits table", () => {
    const base: AutomationReading = { name: "mine", pinned: null, withheld: false, error: null, reading: null };
    expect(noExitsWhy(undefined, true)).toBe("Reading the run…");
    expect(noExitsWhy(base, false)).toContain("No run is pinned");
    expect(noExitsWhy({ ...base, pinned: "builder-mine-2611201000", withheld: true }, false)).toContain("licensed");
    expect(noExitsWhy({ ...base, error: "boom" }, false)).toContain("boom");
  });
});
