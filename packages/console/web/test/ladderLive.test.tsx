import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { LadderLiveClosedLot, LadderLiveOpenLot, LadderLivePayload, LadderLiveStatus } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { LadderLive, LiveModeBadge, modeWords } = await import("../src/pages/Ladder/LadderLive");
const { ladderChains } = await import("../src/pages/Live/LiveTodayOrders");
const { LADDER_SLIDES, NAV_DECL } = await import("../src/lightbox/navGroups");

/**
 * The ladder's Live tab. What is pinned: LIVE and dry-run are told apart and never merged; an absent
 * ledger is an honest empty state; a dead status is not a "ready" or a "not ready"; the money of a sold
 * lot adds up with its fee source beside it; and no account shows unmasked. Every number is invented.
 */

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

const GATE_NAMES = ["live.enabled", "live.gate0_confirmed", "modules.ladder.enabled", "arm.record", "account.designated", "halt.flag", "live.arm", "live.symbols", "live.limits"];

const status = (over: Partial<LadderLiveStatus> = {}): LadderLiveStatus => ({
  ok: true,
  error: null,
  mode: "dry-run",
  note: null,
  today: "2026-11-20",
  ready: false,
  newBuysAllowed: false,
  armedToday: false,
  armedFor: null,
  account: "****4242",
  haltFlag: false,
  unmetGates: ["arm.record"],
  gates: GATE_NAMES.map((name) => ({
    name,
    ok: name !== "arm.record",
    reason: name === "arm.record" ? "arm stamp is None, today is 2026-11-20 -- arming is per-day" : "holds",
    blocks: name === "halt.flag" ? ("new_buys" as const) : ("all" as const),
  })),
  held: { unresolved: [], unrecorded: 0 },
  checks: {
    at: "2026-11-20T09:01:00-05:00",
    date: "2026-11-20",
    positionsReadable: true,
    funds: [{ fund: "TQQQ", ledger: 20, broker: 25, status: "surplus", suspectedSplit: null }],
    blocked: [],
    splitsDeclared: [],
    splitProblems: [],
    intents: [{ what: "place buy TQQQ ldr.b.TQQQ.20261120.1", reason: "level", lot: null, shares: 20, limit: 48.5, tif: "GTC_EXT" }],
  },
  ...over,
});

const open = (over: Partial<LadderLiveOpenLot> = {}): LadderLiveOpenLot => ({
  positionId: "session:TQQQ:2026-11-20:1", arm: "session", symbol: "TQQQ", quantity: 20, buyPrice: 50, buySession: "2026-11-20",
  buyTime: "2026-11-20T09:05:00-05:00", entryValue: -1000, targetPrice: 52, targetStage: "gain", daysHeld: 0, hospital: false,
  account: "****4242", buyFees: 0.02, feesSource: "modeled", sell: { side: "sell", state: "working", limit: 52, tif: "GTC_EXT", final: false },
  ...over,
});

const sold: LadderLiveClosedLot = {
  positionId: "session:TQQQ:2026-11-19:1", arm: "session", symbol: "TQQQ", buySession: "2026-11-19", buyTime: "2026-11-19T09:10:00-05:00",
  buyPrice: 48, buyLevel: 48, refClose: 51, sellSession: "2026-11-20", sellTime: "2026-11-20T10:30:00-05:00", sellPrice: 49.92,
  targetPrice: 49.92, exitReason: "target_gain", holdDays: 1, netPct: 3.99, quantity: 20, price: -48, entryCash: -960, exitCash: 998.4,
  exitKind: "closed", grossPnl: 38.4, fees: 0.07, settlementFees: 0, slippage: 0, netPnl: 38.33, account: "****4242", buyFees: 0.02,
  sellFees: 0.05, feesSource: "broker",
};

const payload = (over: Partial<LadderLivePayload> = {}): LadderLivePayload => ({
  ledger: "ok",
  ledgerError: null,
  today: "2026-11-20",
  openLots: [open(), open({ positionId: "session:SOXL:2026-10-20:1", symbol: "SOXL", daysHeld: 31, hospital: true, sell: null })],
  closedLots: [sold],
  closedTotals: { positions: 1, gross: 38.4, fees: 0.07, settlementFees: 0, slippage: 0, net: 38.33 },
  feesBySource: { modeled: 0, broker: 1, unrecorded: 0 },
  orders: [
    { clientId: "ldr.s.TQQQ.20261120.1", side: "sell", symbol: "TQQQ", lotId: "session:TQQQ:2026-11-20:1", shares: 20, limit: 52, tif: "GTC_EXT", state: "working", final: false, account: "****4242", placedSession: "2026-11-20", endedSession: null, filledShares: null, fillPrice: null, fees: null, feesSource: null, adopted: false },
  ],
  journal: [{ id: 1, tradeDate: "2026-11-20", arm: "session", symbol: "TQQQ", mode: "alert", reason: "lot_past_days session:SOXL:2026-10-20:1", occurrences: 2, firstTs: null, lastTs: null, detail: null }],
  orphans: { at: null, count: 0 },
  lastIteration: { ranAt: 1, phase: "session", status: "ok", ageSeconds: 42 },
  ...over,
});

describe("LIVE and dry-run are told apart", () => {
  it("says dry-run, not live, and sends no suggestion that an order was placed", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).toContain("Dry-run, not live.");
    expect(out).toContain("sends none until live.dry_run is set to false by hand");
    expect(out).not.toContain("Live.</strong>");
  });

  it("says Live only on an explicit live mode", () => {
    const out = html(<LadderLive live={payload()} status={status({ mode: "live" })} />);
    expect(out).toContain("Live.");
    expect(out).toContain("real orders");
    expect(out).not.toContain("Dry-run, not live.");
  });

  it("badges LIVE, DRY-RUN, and an unknown mode as none of those", () => {
    expect(html(<LiveModeBadge status={status({ mode: "live" })} />)).toContain(">LIVE<");
    const dry = html(<LiveModeBadge status={status()} />);
    expect(dry).toContain("DRY-RUN");
    expect(dry).not.toContain("badge-live");
    const dead = html(<LiveModeBadge status={status({ ok: false, error: "x", mode: null })} />);
    expect(dead).toContain("MODE ?");
    expect(dead).not.toContain(">LIVE<");
    expect(modeWords(undefined).tone).toBe("unknown");
  });

  it("is never labelled paper", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out.toLowerCase()).not.toContain("paper");
  });

  it("names the one command that arms, and carries no control", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).toContain("/live-ladder-start");
    // The only buttons are the page chrome every card has: collapse, the help "?", the columns menu, a sort header.
    const chrome = ["btn btn-quiet collapse-toggle collapse-toggle-open", "btn btn-quiet collapse-toggle", "help-q", "mode-btn", "th-sort"];
    const classes = [...out.matchAll(/<button[^>]*class="([^"]*)"/g)].map((m) => m[1]!.trim());
    expect(classes.length).toBeGreaterThan(0);
    for (const c of classes) expect(chrome.some((k) => c.startsWith(k)), `a button of class "${c}"`).toBe(true);
    expect(out).not.toContain("<input");
    expect(out).not.toContain("<form");
  });
});

describe("the state, the gates and the checks", () => {
  it("lists all nine gates with the module's reason for the one that fails", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    for (const g of GATE_NAMES) expect(out).toContain(g);
    expect(out).toContain("arm stamp is None, today is 2026-11-20 -- arming is per-day");
    expect(out).toContain("stops new buys only");
    expect(out).toContain("not ready");
    expect(out).toContain("unmet: arm.record");
  });

  it("shows the cross-check, the standing intents and the masked account", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).toContain("surplus");
    expect(out).toContain("place buy TQQQ ldr.b.TQQQ.20261120.1");
    expect(out).toContain("****4242");
  });

  it("is neither ready nor not ready when the status could not be read", () => {
    const out = html(<LadderLive live={payload()} status={status({ ok: false, error: "ladder live status unavailable", mode: null, ready: null, gates: [], unmetGates: [], checks: null, account: null })} />);
    expect(out).toContain("ladder live status unavailable");
    expect(out).toContain("status unavailable");
    expect(out).toContain("Mode unknown.");
    expect(out).not.toContain("every gate holds");
    expect(out).not.toContain("not ready");
  });

  it("flags held submissions and orphans", () => {
    const out = html(<LadderLive live={payload({ orphans: { at: "2026-11-20T11:00:00-05:00", count: 2 } })} status={status({ held: { unresolved: ["ldr.b.TQQQ.20261120.1"], unrecorded: 1 } })} />);
    expect(out).toContain("an uncertain submit is held");
    expect(out).toContain("last sweep 2026-11-20T11:00:00-05:00");
  });
});

describe("the live ledger", () => {
  it("is an honest empty state, not an error, where there is none", () => {
    const out = html(<LadderLive live={payload({ ledger: "absent", openLots: [], closedLots: [], orders: [], journal: [], lastIteration: null })} status={status()} />);
    expect(out).toContain("no live ledger");
    expect(out).toContain("live_trades.db");
    expect(out).not.toContain("could not be read");
    expect(out).not.toContain("open live lots");
  });

  it("says when the ledger could not be read", () => {
    const out = html(<LadderLive live={payload({ ledger: "failed", ledgerError: "file is not a database" })} status={status()} />);
    expect(out).toContain("The live ledger could not be read: file is not a database");
  });

  it("shows a dry-run machine's empty book as empty", () => {
    const out = html(<LadderLive live={payload({ openLots: [], closedLots: [], orders: [] })} status={status()} />);
    expect(out).toContain("no live lot is held");
    expect(out).toContain("no live lot has sold yet");
    expect(out).toContain("no order recorded");
  });

  it("shows open lots with cost, target, age, the hospital flag, the resting sell and its fee source", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).toContain("20 × 50.00");
    expect(out).toContain("-$1,000.00".replace(",", "")); // whole-position cash, signed
    expect(out).toContain("52.00 · +4%");
    expect(out).toContain("past 18 d");
    expect(out).toContain("working · 52.00 · GTC_EXT");
    expect(out).toContain("modeled (the schedule)");
  });

  it("shows a sold lot adding up, with its fees' source in their own column", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).toContain("+$998.40");
    expect(out).toContain("-$960.00");
    expect(out).toContain("$38.40"); // gross
    expect(out).toContain("$0.07"); // fees
    expect(out).toContain("$38.33"); // net
    expect(out).toContain("fees from");
    expect(out).toContain(">broker<");
    expect(out).toContain("fees: 0 modeled, 1 from the broker"); // the footer counts the sources
  });

  it("shows no account unmasked", () => {
    const out = html(<LadderLive live={payload()} status={status()} />);
    expect(out).not.toContain("5WZ04242");
    expect(out.match(/\*{4}\d{4}/g)!.length).toBeGreaterThan(0);
  });
});

describe("the ladder's place on the Live page and in the tabs", () => {
  it("has a live tab in the nav, beside today", () => {
    expect(LADDER_SLIDES.map((s) => s.id)).toContain("live");
    const g = NAV_DECL["ladder"];
    expect(g.groups.find((x) => x.label === "today")!.ids).toEqual(["summary", "live"]);
    const grouped = g.groups.flatMap((x) => x.ids);
    expect(new Set(grouped)).toEqual(new Set(LADDER_SLIDES.map((s) => s.id)));
  });

  it("draws the loop's day as chains: a held lot with its resting sell, a sold lot with its net", () => {
    const chains = ladderChains(payload(), "2026-11-20");
    expect(chains.map((c) => [c.title, c.open, c.net])).toEqual([
      ["TQQQ lot", true, null],
      ["SOXL lot", true, null],
      ["TQQQ lot", false, 38.33],
    ]);
    expect(chains[0]!.steps.map((s) => s.role)).toEqual(["open", "working"]);
    expect(chains[2]!.steps.map((s) => s.cash)).toEqual([-960, 998.4]);
    expect(chains[0]!.netLabel).toBe("held, unmarked");
  });
});
