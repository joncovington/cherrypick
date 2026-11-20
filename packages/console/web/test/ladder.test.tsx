import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { LadderArmSummary, LadderHistoryRow, LadderOpenLot, LadderPayload, LadderStatus, LadderSummary } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { LadderToday, LadderPositions, LadderArms, LadderHelp } = await import("../src/pages/Ladder/LadderPages");
const { bidFromLevel, lotModel, targetText, whenEt } = await import("../src/pages/Ladder/ladderModels");
const { LADDER_SLIDES } = await import("../src/lightbox/navGroups");
const { isModuleVisible } = await import("../src/lib/visibility");

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

const lot = (over: Partial<LadderOpenLot> = {}): LadderOpenLot => ({
  positionId: "once:TQQQ:2026-11-19:1",
  arm: "once",
  symbol: "TQQQ",
  quantity: 20,
  buyPrice: 46.5,
  buySession: "2026-11-19",
  buyTime: "2026-11-19T16:01:31-05:00",
  entryValue: -930,
  targetPrice: 48.36,
  targetStage: "gain",
  lastBid: 46.1,
  lastMarkTime: "2026-11-19T19:59:46-05:00",
  valueAtBid: 922,
  unrealisedAtBid: -8,
  daysHeld: 1,
  hospital: false,
  ...over,
});

const payload = (over: Partial<LadderPayload> = {}): LadderPayload => ({
  arms: [{ arm: "chain", summary: "chain summary text", entry: "chain", target: "fixed", enabled: true }, { arm: "session-decay", summary: "fifth arm text", entry: "session", target: "decay", enabled: true }],
  session: "2026-11-20",
  dbPresent: true,
  today: "2026-11-20",
  openLots: [lot(), lot({ positionId: "decay:SOXL:2026-10-20:1", arm: "decay", symbol: "SOXL", buySession: "2026-10-20", daysHeld: 31, hospital: true, targetStage: "breakeven", lastBid: null, valueAtBid: null, unrealisedAtBid: null })],
  measurementBreaks: [],
  lastIteration: { ranAt: 1, phase: "session", status: "ok", ageSeconds: 42 },
  ...over,
});

const status = (over: Partial<LadderStatus> = {}): LadderStatus => ({
  ok: true,
  error: null,
  date: "2026-11-20",
  hours: "08:15-20:00 ET",
  inHours: true,
  arms: { chain: { entry: "chain", target: "fixed" }, session: { entry: "session", target: "fixed" } },
  funds: { TQQQ: { bid: 47.41, ask: 47.42, refClose: 49.01, nextBuy: { chain: 45.34, session: null } } },
  ...over,
});

const arm = (over: Partial<LadderArmSummary> = {}): LadderArmSummary => ({
  lots: 8, closed: 6, open: 2, netPnl: 262.99, grossPnl: 263.38, fees: 0.39, unrealisedAtBid: -62.05, soldWithin18d: 6, inHospital: 0,
  medianHoldDays: 1, longestClosedHoldDays: 3, oldestOpenDays: 0, symbols: ["SOXL", "TQQQ"], ...over,
});

const sold: LadderHistoryRow = {
  positionId: "session:TQQQ:2026-11-12:1", arm: "session", symbol: "TQQQ", buySession: "2026-11-12", buyTime: "2026-11-12T10:00:00-05:00",
  buyPrice: 40, buyLevel: 40.5, refClose: 43, sellSession: "2026-11-14", sellTime: "2026-11-14T09:30:00-05:00", sellPrice: 41.6,
  targetPrice: 41.6, exitReason: "target_gain", holdDays: 2, netPct: 3.99, quantity: 20, price: -40, entryCash: -800, exitCash: 832,
  exitKind: "closed", grossPnl: 32, fees: 0.05, settlementFees: 0, slippage: 0, netPnl: 31.95,
};

describe("ladder's today tab", () => {
  it("says the module has not run when there is no store", () => {
    expect(html(<LadderToday data={{ ...payload(), dbPresent: false }} status={undefined} loading={false} />)).toContain("has not run on this machine");
  });

  it("shows each fund's quote and previous close, each arm's next level and how far the bid is from it", () => {
    const out = html(<LadderToday data={payload()} status={status()} loading={false} />);
    expect(out).toContain("45.34");
    expect(out).toContain("49.01"); // previous close
    expect(out).toContain("4.6% above"); // (47.41 - 45.34) / 45.34
    expect(out).toContain("1 past 18 days") ;
  });

  it("shows a dash, not a level, for an arm that has none", () => {
    const out = html(<LadderToday data={payload()} status={status()} loading={false} />);
    expect(out).toMatch(/<td>session<\/td><td>49\.01<\/td><td>—<\/td><td[^>]*>—<\/td>/);
  });

  it("says the module's read could not be had rather than drawing an empty table", () => {
    const out = html(<LadderToday data={payload()} status={status({ ok: false, error: "ladder read unavailable — boom", funds: {} })} loading={false} />);
    expect(out).toContain("ladder read unavailable — boom");
  });

  it("states that the rule is not a tested edge", () => {
    expect(html(<LadderToday data={payload()} status={status()} loading={false} />)).toContain("Not a tested edge");
  });
});

describe("ladder's open lots", () => {
  it("lists a lot's arm, fund, shares and price, target, days, bid and value, and flags the old one", () => {
    const out = html(<LadderPositions data={payload()} />);
    expect(out).toContain("20 × 46.50");
    expect(out).toContain("48.36 · +4%");
    expect(out).toContain("$922.00");
    expect(out).toContain("11-19 16:01");
    expect(out).toContain("31 days");
    expect(out).toContain("hospital");
    expect(out.match(/chip-warn/g)?.length).toBe(1);
  });

  it("names the decay arm's stepped target", () => {
    expect(targetText({ targetPrice: 55.8, targetStage: "breakeven" })).toBe("55.80 · breakeven");
  });

  it("says no lot is held on an empty book", () => {
    expect(html(<LadderPositions data={payload({ openLots: [] })} />)).toContain("no lot is held");
  });
});

describe("ladder's arms tab", () => {
  const summary = (): LadderSummary => ({ ok: true, error: null, arms: { chain: arm(), session: arm({ netPnl: -10, unrealisedAtBid: 4, inHospital: 2 }) } });

  it("shows realised and unrealised and the two together, in the module's order", () => {
    const out = html(<LadderArms summary={summary()} status={status()} />);
    expect(out.indexOf("chain")).toBeLessThan(out.indexOf("session"));
    expect(out).toContain("$262.99");
    expect(out).toContain("-$62.05");
    expect(out).toContain("$200.94"); // 262.99 - 62.05
    expect(out).toContain("-$6.00"); // -10 + 4
  });

  it("describes every declared arm from the config, a fifth included, with no code change", () => {
    const out = html(<LadderArms summary={summary()} status={status()} decl={payload().arms} />);
    expect(out).toContain("fifth arm text");
    expect(out).toContain("session-decay");
    expect(out).toContain("chain summary text");
  });

  it("says the module's summary could not be read", () => {
    expect(html(<LadderArms summary={{ ok: false, error: "ladder read unavailable — boom", arms: {} }} status={undefined} />)).toContain("ladder read unavailable — boom");
  });
});

describe("ladder's help", () => {
  it("says plainly that the rule is not a tested edge and names the four arms and the verdicts", () => {
    const out = html(<LadderHelp status={status()} />);
    expect(out).toContain("not a tested edge");
    for (const arm of ["session", "chain", "once", "decay"]) expect(out).toContain(arm);
    expect(out).toContain("Item 26");
    expect(out).toContain("Item 27");
    expect(out).toContain("Item 95");
    expect(out).toContain("Holm");
    expect(out).toContain("Item 96");
    expect(out).toContain("no gain on TQQQ");
    expect(out).toContain("$0.0008 per share");
  });

  it("writes rates with per, never a", () => {
    const out = html(<LadderHelp status={status()} />);
    expect(out).not.toMatch(/\b(a|one lot a) (session|day|minute|share)\b/);
  });
});

describe("ladder's models", () => {
  it("reads the bid against the level", () => {
    expect(bidFromLevel(47.41, 45.34)?.text).toBe("4.6% above");
    expect(bidFromLevel(45, 45.34)).toEqual({ text: "0.7% under it", tone: "pos" });
    expect(bidFromLevel(45.34, 45.34)?.text).toBe("at the level");
    expect(bidFromLevel(null, 45.34)).toBeNull();
    expect(bidFromLevel(45, null)).toBeNull();
  });

  it("writes a time on the ET clock from the offset the module wrote", () => {
    expect(whenEt("2026-11-19", "2026-11-19T16:01:31-05:00")).toBe("11-19 16:01");
    expect(whenEt("2026-11-19", "2026-11-19T21:01:31Z")).toBe("11-19 16:01");
  });

  it("draws a sold lot as the buy and the sale, with the costs apart", () => {
    const m = lotModel(sold);
    expect(m.legs[0]).toMatchObject({ buy: true, tag: "BUY", text: "20 TQQQ @ 40.00" });
    expect(m.legs[0]!.quote).toContain("filled lower on a gap");
    expect(m.cash).toBe(31.95);
    expect(m.after.join(" ")).toContain("exit +$832.00");
    expect(m.after.join(" ")).toContain("fees $0.05");
    expect(m.side[0]).toBe("net +3.99% of cost");
  });
});

describe("ladder's place in the console", () => {
  it("has a tab for each page the spec names", () => {
    expect(LADDER_SLIDES.map((s) => s.id)).toEqual(["summary", "live", "positions", "history", "arms", "decisions", "guide"]);
  });

  it("is hidden when the suite says its module is off, shown when unknown", () => {
    const off = { ok: true, capabilities: {}, modules: { ladder: { configured: false, enabled: false, missing: [] } }, services: {}, features: {} } as never;
    expect(isModuleVisible("ladder", off)).toBe(false);
    expect(isModuleVisible("ladder", undefined)).toBe(true);
  });
});
