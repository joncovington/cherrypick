import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ChangeRow, ModulePerformanceResult } from "@console/shared";
import { VsControl, vsControlByArm } from "../src/pages/Meic/MeicPerformanceTab";
import { PerformanceSlide } from "../src/components/performance/PerformanceSlide";

const html = (el: React.ReactElement, client = new QueryClient()) =>
  renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const GAP = { per_session: 400, per_session_ci: [100, 700] as [number, number], per_trade: -30.99, per_trade_ci: [-79.25, 11.47] as [number, number], level: 0.95, first: null, last: null };

function row(over: Partial<ChangeRow>): ChangeRow {
  return { kind: "arm", name: "bp-10k", description: null, id: null, arm: "bp-10k", against: "control", status: null, gap: GAP, reading: "leaning", basis: "per_trade", direction: "worse", sessions: 14, sessions_to_decision: 6, ...over };
}

describe("MEIC's arms in one table", () => {
  it("keys each arm's line against control by its tag, leaving declared items out", () => {
    const m = vsControlByArm([row({}), row({ kind: "declared", arm: "bp-10k", name: "x" }), row({ kind: "advised", arm: "advised:late" })]);
    expect([...m.keys()]).toEqual(["bp-10k", "advised:late"]);
    expect(m.get("bp-10k")?.kind).toBe("arm");
  });

  it("shows the gap per trade and the reading, the interval in its title, and a dash without one", () => {
    expect(html(<VsControl row={row({})} arm="bp-10k" />)).toContain("-$30.99/trade");
    expect(html(<VsControl row={row({})} arm="bp-10k" />)).toContain('title="-$30.99 a trade [-$79.25, $11.47]');
    expect(html(<VsControl row={row({})} arm="bp-10k" />)).toContain("leaning worse");
    expect(html(<VsControl row={undefined} arm="bp-5k" />)).toContain("—");
    expect(html(<VsControl row={undefined} arm="control" />)).toContain("—");
  });

  it("draws no per-arm cards where the module compares its arms in a table of its own", () => {
    const perf: ModulePerformanceResult = {
      ok: true,
      module: "meic",
      mode: "paper",
      schema: "meic_ic",
      era: { key: "current", from: null, note: null, moduleEra: null },
      nRecords: 2,
      groups: [{ tag: "control", reading: { sample: 2 }, sessionNets: [["2026-11-20", 10]], tradeNets: [5, 5] }],
      exitReasons: [],
      heldBack: [],
      pairs: [],
      breaks: [],
      excursions: { ok: false } as ModulePerformanceResult["excursions"],
      error: null,
    };
    const client = () => {
      const c = new QueryClient();
      c.setQueryData(["performance", "meic", "current", "paper"], perf);
      return c;
    };
    expect(html(<PerformanceSlide module="meic" />, client())).toContain("How has each arm done?");
    const out = html(<PerformanceSlide module="meic" armCards={false} />, client());
    expect(out).not.toContain("How has each arm done?");
    expect(out).toContain("cumulative net P&amp;L");
  });
});
