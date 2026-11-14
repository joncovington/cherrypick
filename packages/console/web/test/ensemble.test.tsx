import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { EnsembleEntryRow, EnsembleHistoryRow, EnsembleLeg, EnsembleLine, EnsemblePayload, EnsemblePerformance, EnsemblePosition } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { EnsembleToday, EnsemblePositions, EnsemblePerformancePage, EnsembleCosts, EnsembleHelp } = await import("../src/pages/Ensemble/EnsemblePages");
const { closedModel, dailyCurve, gateWords, openModel, shapeWords, todayCounts } = await import("../src/pages/Ensemble/ensembleModels");
const { ENSEMBLE_SLIDES } = await import("../src/lightbox/navGroups");
const { isModuleVisible } = await import("../src/lib/visibility");

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

// An arm name the module does not ship: nothing on the page may need to know it in advance.
const ARM = "zz_custom_arm";

const CONDOR: EnsembleLeg[] = [
  { role: "short_put", right: "P", side: -1, strike: 5900 },
  { role: "long_put", right: "P", side: 1, strike: 5890 },
  { role: "short_call", right: "C", side: -1, strike: 6100 },
  { role: "long_call", right: "C", side: 1, strike: 6110 },
];

const position = (over: Partial<EnsemblePosition> = {}): EnsemblePosition => ({
  positionId: `${ARM}:2026-11-13`,
  arm: ARM,
  symbol: "AAA",
  status: "open",
  expiration: "2026-11-13",
  entrySession: "2026-11-13",
  entryTime: null,
  quantity: 2,
  entrySpot: 6000,
  entryMid: 1.3,
  capital: 1740,
  entryValue: 260,
  entryFees: 2.6,
  entrySlippage: 5,
  exitReason: null,
  exitSession: null,
  exitSpot: null,
  exitValue: null,
  settlePrice: null,
  grossPnl: null,
  fees: null,
  settlement: null,
  slippage: null,
  netPnl: null,
  netStress: null,
  netMid: null,
  legs: CONDOR,
  mark: { ts: "2026-11-13T10:30:00", value: -170, spot: 6020 },
  unrealised: 90,
  ...over,
});

const row = (over: Partial<EnsembleEntryRow>): EnsembleEntryRow => ({
  arm: ARM,
  symbol: "AAA",
  entrySession: "2026-11-13",
  outcome: "pending",
  refusal: null,
  positionId: null,
  decidedAt: null,
  gate: null,
  ...over,
});

const payload = (over: Partial<EnsemblePayload> = {}): EnsemblePayload => ({
  session: "2026-11-13",
  dbPresent: true,
  arms: ["alpha", "beta", ARM],
  positions: [position()],
  today: [
    row({ arm: "alpha", outcome: "entered", positionId: "alpha:2026-11-13" }),
    row({ arm: "beta", outcome: "pending", refusal: "gate:gex_ratio", gate: { reason: "gate:gex_ratio", check: "gex_ratio", value: "1.8", occurrences: 7 } }),
    row({ arm: ARM, outcome: "missed", refusal: "gate:sd_change", gate: { reason: "gate:sd_change", check: "sd_change", value: "0.31", occurrences: 4 } }),
  ],
  measurementBreaks: [],
  lastIteration: null,
  ...over,
});

const line = (over: Partial<EnsembleLine>): EnsembleLine => ({
  trades: 2,
  net: 100,
  gross: 130,
  fees: 5,
  settlement: 1,
  slippage: 24,
  netStress: 110,
  netMid: 125,
  wins: 1,
  mean: 50,
  worst: -40,
  drawdown: 40,
  curve: [],
  ...over,
});

const perf: EnsemblePerformance = {
  dbPresent: true,
  arms: {
    alpha: line({ net: 76, gross: 100, fees: 7.8, settlement: 1.2, slippage: 15, drawdown: 127.6 }),
    [ARM]: line({ net: -25.3, gross: 0, fees: 7.8, settlement: 1.5, slippage: 16, drawdown: 131.9 }),
  },
  combined: line({ net: 50.7, gross: 100, fees: 15.6, settlement: 2.7, slippage: 31, drawdown: 259.5, curve: [{ x: "2026-11-04", y: -25.2 }, { x: "2026-11-04", y: -157.1 }, { x: "2026-11-05", y: 50.7 }] }),
};

const closed = (over: Partial<EnsembleHistoryRow> = {}): EnsembleHistoryRow => ({
  positionId: `${ARM}:2026-11-04`,
  arm: ARM,
  symbol: "AAA",
  expiration: "2026-11-05",
  entrySession: "2026-11-04",
  exitSession: "2026-11-05",
  exitReason: "settlement",
  quantity: 1,
  price: 1.2,
  entryCash: 120,
  exitCash: -10,
  exitKind: "settled",
  grossPnl: 110,
  fees: 2.6,
  settlementFees: 1.2,
  slippage: 5,
  netPnl: 101.2,
  netStress: 104.7,
  netMid: 111.4,
  legs: CONDOR,
  ...over,
});

describe("ensemble's tabs", () => {
  it("say the module has not run when there is no store", () => {
    expect(html(<EnsembleToday data={{ ...payload(), dbPresent: false }} loading={false} />)).toContain("has not run on this machine");
  });

  it("show each arm due today and the gate that blocked it, with the value it read", () => {
    const out = html(<EnsembleToday data={payload()} loading={false} />);
    expect(out).toContain("alpha");
    expect(out).toContain("gate:gex_ratio read gex_ratio = 1.8");
    expect(out).toContain("gate:sd_change read sd_change = 0.31");
    expect(out).toContain(ARM);
    expect(out).toContain("open structures");
  });

  it("draw an open structure with every leg in the order chain, sold and bought", () => {
    const out = html(<EnsemblePositions data={payload()} />);
    expect(out).toContain("pos-chain");
    expect(out).toContain("arm zz_custom_arm");
    expect(out).toContain(">STO<");
    expect(out).toContain(">BTO<");
    for (const strike of ["5890P", "5900P", "6100C", "6110C"]) expect(out).toContain(`2 2026-11-13 ${strike}`);
    expect(out).toContain("+$90.00"); // unrealised: 260 credit less the 170 it would cost to close
  });

  it("show each arm's net and drawdown, the stacked line apart from them, and stress and mid as context", () => {
    const out = html(<EnsemblePerformancePage perf={perf} />);
    expect(out).toContain("alpha");
    expect(out).toContain(ARM);
    expect(out).toContain("all arms");
    expect(out).toContain("-$127.60");
    expect(out).toContain("-$131.90");
    expect(out).toContain("-$259.50");
    expect(out).toContain("net at stress");
    expect(out).toContain("net at mid");
    expect(out).toContain("suite&#x27;s slippage model");
  });

  it("keep fees, settlement and slippage apart on every arm, each subtracted once", () => {
    const out = html(<EnsembleCosts perf={perf} />);
    expect(out).toContain("-$15.60");
    expect(out).toContain("-$2.70");
    expect(out).toContain("-$31.00");
    for (const [, l] of [...Object.entries(perf.arms), ["all", perf.combined] as const]) {
      expect(l.gross - l.fees - l.settlement - l.slippage).toBeCloseTo(l.net, 2);
    }
  });

  it("explain that the arms are not a fixed list and that stress and mid are context", () => {
    const out = html(<EnsembleHelp />);
    expect(out).toContain("not a fixed list");
    expect(out).toContain("context");
    expect(out).toContain("posted close");
  });
});

describe("ensemble's models", () => {
  it("words the blocking gate, and falls back to the refusal alone", () => {
    expect(gateWords(payload().today[1]!)).toBe("gate:gex_ratio read gex_ratio = 1.8");
    expect(gateWords(row({ refusal: "no_tick_in_window" }))).toBe("no_tick_in_window");
  });

  it("counts entered, pending and missed", () => {
    expect(todayCounts(payload().today)).toEqual({ entered: 1, pending: 1, missed: 1 });
  });

  it("describes a shape from its legs alone", () => {
    expect(shapeWords(CONDOR)).toBe("4 legs · 2 sold, 2 bought · C/P");
    expect(shapeWords([])).toBe("legs not recorded");
  });

  it("keeps one point a session on the chart, the last of the day", () => {
    expect(dailyCurve(perf.combined)).toEqual([{ x: "2026-11-04", y: -157.1 }, { x: "2026-11-05", y: 50.7 }]);
  });

  it("puts an open structure's unrealised on its card and every leg in its chain", () => {
    const m = openModel(position());
    expect(m.cash).toBe(90);
    expect(m.legs).toHaveLength(4);
    expect(m.legs.filter((l) => l.buy)).toHaveLength(2);
    expect(m.legs[0]!.text).toBe("2 2026-11-13 5900P");
  });

  it("states a closed structure's exit and its three costs apart, net on the card", () => {
    const m = closedModel(closed());
    expect(m.cash).toBe(101.2);
    expect(m.after.join(" ")).toContain("held to the posted close · exit -$10.00");
    expect(m.after.join(" ")).toContain("fees $2.60 · settlement $1.20 · slippage $5.00");
    expect(m.legs).toHaveLength(4);
  });
});

describe("ensemble's place in the console", () => {
  it("has a tab for each page the spec names", () => {
    expect(ENSEMBLE_SLIDES.map((s) => s.id)).toEqual(["summary", "positions", "history", "decisions", "performance", "costs", "guide"]);
  });

  it("is hidden when the suite says its module is off, shown when unknown", () => {
    const off = { ok: true, capabilities: {}, modules: { ensemble: { configured: false, enabled: false, missing: [] } }, services: {}, features: {} } as never;
    expect(isModuleVisible("ensemble", off)).toBe(false);
    expect(isModuleVisible("ensemble", undefined)).toBe(true);
  });
});
