import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  WeeklyputCycle,
  WeeklyputCycles,
  WeeklyputEntryRow,
  WeeklyputPayload,
  WeeklyputPosition,
  WeeklyputStatus,
  WeeklyputSummary,
} from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { WeeklyputToday, WeeklyputCycles: CyclesPage, WeeklyputPerformance, WeeklyputCosts, WeeklyputHelp, WeeklyputPositions } = await import(
  "../src/pages/Weeklyput/WeeklyputPages"
);
const { cumulativeBp, cycleRows, distanceToStrike, pickCycle, putModel } = await import("../src/pages/Weeklyput/weeklyputModels");
const { WEEKLYPUT_SLIDES } = await import("../src/lightbox/navGroups");
const { isModuleVisible } = await import("../src/lib/visibility");

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

const status = (over: Partial<WeeklyputStatus> = {}): WeeklyputStatus => ({
  ok: true,
  error: null,
  date: "2026-11-13",
  decisionWindow: "15:45-15:55 ET",
  entryToday: "2026-11-20",
  nextCycle: "2026-11-20",
  nextEntrySession: "2026-11-13",
  arms: ["unhedged"],
  names: ["SPX", "SPY", "QQQ", "IWM"],
  awaitingClose: [],
  ...over,
});

const entry = (over: Partial<WeeklyputEntryRow>): WeeklyputEntryRow => ({
  arm: "unhedged",
  symbol: "SPX",
  expiration: "2026-11-20",
  entrySession: "2026-11-13",
  outcome: "pending",
  refusal: null,
  positionId: null,
  decidedAt: null,
  ...over,
});

const put = (over: Partial<WeeklyputPosition> = {}): WeeklyputPosition => ({
  positionId: "unhedged:QQQ:2026-11-20",
  arm: "unhedged",
  symbol: "QQQ",
  status: "open",
  expiration: "2026-11-20",
  entrySession: "2026-11-13",
  entryTime: null,
  strike: 500,
  quantity: 1,
  settlementStyle: "physical",
  entrySpot: 520,
  entryForward: 520,
  entryDelta: 0.098,
  entryBid: 1.0,
  entryAsk: 1.2,
  entryMid: 1.1,
  entryValue: 110,
  entryFees: 0.65,
  entrySlippage: 2,
  notional: 52000,
  settleSession: null,
  settlePrice: null,
  exitValue: null,
  exitReason: null,
  settlement: null,
  grossPnl: null,
  fees: null,
  slippage: null,
  netPnl: null,
  netBp: null,
  assignmentExposedAt: null,
  mark: { ts: "2026-11-16T10:00:00", mid: 0.75, value: -75, spot: 512, extrinsic: 0.75 },
  ...over,
});

const payload = (over: Partial<WeeklyputPayload> = {}): WeeklyputPayload => ({
  session: "2026-11-13",
  dbPresent: true,
  positions: [put()],
  entries: [entry({ symbol: "QQQ", outcome: "entered", positionId: "unhedged:QQQ:2026-11-20" }), entry({ symbol: "IWM", outcome: "missed", refusal: "delta_not_bracketed" })],
  measurementBreaks: [],
  lastIteration: null,
  ...over,
});

const cycle = (over: Partial<WeeklyputCycle>): WeeklyputCycle => ({
  arm: "unhedged",
  expiration: "2026-11-06",
  entrySession: "2026-10-30",
  names: {},
  unentered: {},
  finished: true,
  cycleNetBp: 2,
  netPnl: 10,
  ...over,
});

describe("weeklyput's tabs", () => {
  it("say the module has not run when there is no store", () => {
    expect(html(<WeeklyputToday data={{ ...payload(), dbPresent: false }} status={undefined} loading={false} />)).toContain("has not run on this machine");
  });

  it("show today's state, each name's row for the cycle and the open puts", () => {
    const out = html(<WeeklyputToday data={payload()} status={status()} loading={false} />);
    expect(out).toContain("entry session");
    expect(out).toContain("selling into the 2026-11-20 expiry");
    expect(out).toContain("cycle 2026-11-20");
    expect(out).toContain("delta_not_bracketed");
    expect(out).toContain("open puts");
    expect(out).toContain("assignment");
    expect(out).toContain("+2.34%"); // (512 - 500) / 512, out of the money
  });

  it("names the next cycle and its entry session on a day with no entry", () => {
    const out = html(<WeeklyputToday data={payload()} status={status({ entryToday: null, date: "2026-11-16" })} loading={false} />);
    expect(out).toContain("entered on 2026-11-13");
  });

  it("says the module's calendar could not be read rather than guessing", () => {
    const out = html(<WeeklyputToday data={payload()} status={{ ...status(), ok: false, error: "weeklyput read unavailable — boom" }} loading={false} />);
    expect(out).toContain("weeklyput read unavailable — boom");
    expect(out).toContain("could not be read");
  });

  it("shows the cycles as the judged unit, with the module's own mean and the names it left out", () => {
    const cycles: WeeklyputCycles = {
      ok: true,
      error: null,
      cycles: [
        cycle({
          names: {
            SPX: { status: "closed", strike: 6350, entryDelta: 0.1, entryValue: 184, exitValue: 0, grossPnl: 184, fees: 0.65, settlement: 0, slippage: 5, netPnl: 178.35, netBp: 2.7, exitReason: "expired", assignmentExposed: false },
          },
          unentered: { IWM: { outcome: "missed", refusal: "delta_not_bracketed" } },
          cycleNetBp: 2.7,
          netPnl: 178.35,
        }),
        cycle({ expiration: "2026-11-13", finished: false, cycleNetBp: null, netPnl: null }),
      ],
    };
    const out = html(<CyclesPage cycles={cycles} status={status()} />);
    expect(out).toContain("is the unit this module is judged on");
    expect(out).toContain("+2.7 bp");
    expect(out).toContain("+$178.35");
    expect(out).toContain("IWM (missed: delta_not_bracketed)");
    expect(out).toContain("not yet");
  });

  it("calls fewer than 80 cycles a forward read, not a verdict", () => {
    const summary: WeeklyputSummary = {
      ok: true,
      error: null,
      arms: {
        unhedged: {
          cycles: 3, meanBp: 4.2, stdevBp: 1.1, positiveShare: 0.667, worst: { expiration: "2026-11-06", cycleNetBp: -10.6 },
          byName: { SPX: { trades: 3, meanBp: 5 } },
          money: { entryValue: 500, exitValue: -100, fees: 3, settlement: 2, slippage: 9, grossPnl: 400, netPnl: 386, feeDrag: 0.01 },
          missed: 1,
        },
      },
    };
    const out = html(<WeeklyputPerformance summary={summary} cycles={{ ok: true, error: null, cycles: [] }} />);
    expect(out).toContain("a forward read, not a verdict");
    expect(out).toContain("below it");
    expect(out).toContain("+4.2 bp");
    expect(out).toContain("67%");
    expect(out).toContain("-10.6 bp");
  });

  it("keeps fees, settlement and slippage apart, and the fee drag on trading fees alone", () => {
    const summary: WeeklyputSummary = {
      ok: true,
      error: null,
      arms: {
        unhedged: {
          cycles: 1, meanBp: 1, stdevBp: null, positiveShare: 1, worst: null, byName: {},
          money: { entryValue: 200, exitValue: 0, fees: 2, settlement: 6, slippage: 9, grossPnl: 200, netPnl: 183, feeDrag: 0.04 },
          missed: 0,
        },
      },
    };
    const out = html(<WeeklyputCosts summary={summary} history={[]} />);
    expect(out).toContain("settlement");
    expect(out).toContain("slippage");
    // 2 / 200: settlement (6) is not in it, as it is not on every other surface.
    expect(out).toContain("1.0%");
    expect(out).not.toContain("4.0%");
  });

  it("explains the rule, item 46 to item 69, and what paper adds, with no study figure", () => {
    const out = html(<WeeklyputHelp status={status()} />);
    for (const w of ["item 46", "item 57", "item 69", "delta_not_bracketed", "posted close", "assignment", "missed"]) expect(out.toLowerCase()).toContain(w);
    expect(out).not.toMatch(/\d+(\.\d+)?\s?%\s?(win|positive)/i);
  });

  it("draws open puts as order-chain cards, an STO leg at the mid", () => {
    const out = html(<WeeklyputPositions data={payload()} />);
    expect(out).toContain("pos-chain");
    expect(out).toContain("pos-leg-act is-sell");
    expect(out).toContain(">STO<");
    expect(out).toContain("1 2026-11-20 500P @ 1.10");
  });
});

describe("weeklyput's models", () => {
  it("picks today's entry cycle, else the held one, else the next", () => {
    const entries = [entry({ expiration: "2026-11-20", entrySession: "2026-11-13" }), entry({ expiration: "2026-11-06", entrySession: "2026-10-30" })];
    expect(pickCycle(status(), entries)).toBe("2026-11-20");
    expect(pickCycle(status({ entryToday: null, date: "2026-11-16" }), entries)).toBe("2026-11-20");
    expect(pickCycle(status({ entryToday: null, date: "2026-11-23", nextCycle: "2026-12-04" }), entries)).toBe("2026-12-04");
    expect(pickCycle(undefined, [])).toBeNull();
  });

  it("gives every declared name a row, 'none' where the register has no row yet", () => {
    const rows = cycleRows("2026-11-20", ["SPX", "QQQ", "IWM"], payload().entries, payload().positions);
    expect(rows.map((r) => [r.symbol, r.outcome])).toEqual([["SPX", "none"], ["QQQ", "entered"], ["IWM", "missed"]]);
    expect(rows[1]!.position?.strike).toBe(500);
  });

  it("measures spot from the strike, positive out of the money", () => {
    expect(distanceToStrike(put())).toBeCloseTo(12 / 512, 6);
    expect(distanceToStrike(put({ mark: { ts: "", mid: null, value: null, spot: 490, extrinsic: null } }))).toBeLessThan(0);
    expect(distanceToStrike(put({ mark: null }))).toBeNull();
  });

  it("sums only finished cycles, oldest first, from the module's own mean", () => {
    const pts = cumulativeBp([
      cycle({ expiration: "2026-11-13", cycleNetBp: -3 }),
      cycle({ expiration: "2026-11-06", cycleNetBp: 5 }),
      cycle({ expiration: "2026-11-20", finished: false, cycleNetBp: null }),
    ]);
    expect(pts).toEqual([{ x: "2026-11-06", y: 5 }, { x: "2026-11-13", y: 2 }]);
  });

  it("states a settled put's exit and its three costs apart", () => {
    const m = putModel(
      put({ status: "closed", exitReason: "assigned", exitValue: -240, grossPnl: -150, fees: 0.65, settlement: 2, slippage: 3, netPnl: -155.65, netBp: -23.9, settlePrice: 487, settleSession: "2026-11-23", mark: null }),
    );
    expect(m.cash).toBe(-155.65);
    expect(m.after.join(" ")).toContain("assigned");
    expect(m.after.join(" ")).toContain("fees $0.65 · settlement $2.00 · slippage $3.00");
    expect(m.legs[0]).toMatchObject({ buy: false, tag: "STO" });
  });
});

describe("weeklyput's place in the console", () => {
  it("has a tab for each page the spec names", () => {
    expect(WEEKLYPUT_SLIDES.map((s) => s.id)).toEqual(["summary", "cycles", "positions", "history", "decisions", "performance", "costs", "guide"]);
  });

  it("is hidden when the suite says its module is off, shown when unknown", () => {
    const off = { ok: true, capabilities: {}, modules: { weeklyput: { configured: false, enabled: false, missing: [] } }, services: {}, features: {} } as never;
    expect(isModuleVisible("weeklyput", off)).toBe(false);
    expect(isModuleVisible("weeklyput", undefined)).toBe(true);
  });
});
