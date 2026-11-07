import { beforeEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageCandidate, ManageCandidates, ManageTrade } from "@console/shared";
import { sortRows, type SortOption } from "../src/components/FilterSort";
import { AddingTradesCard, INCOME_SORTS } from "../src/pages/Live/IncomeCard";
import { POSITION_SORTS } from "../src/pages/Live/PositionCards";
import { writePref } from "../src/lib/prefs";

/**
 * Filter & sort (2026-11-06): the sort helper keeps unknowns last and ties in order, the positions
 * board sorts by the URL's choice and counts a changed sort on its button, and adding trades says
 * what is on in its summary line and sorts by the remembered choice.
 */

const text = (html: string) => html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("sortRows", () => {
  const opt: SortOption<{ n: string; v: number | null }> = { id: "v", label: "v", hint: "low first", key: (r) => r.v };
  const rows = [
    { n: "a", v: 3 },
    { n: "b", v: null },
    { n: "c", v: 1 },
    { n: "d", v: 3 },
  ];
  it("orders by the key, keeps ties in their order, and puts an unknown last either way", () => {
    expect(sortRows(rows, opt, false).map((r) => r.n)).toEqual(["c", "a", "d", "b"]);
    expect(sortRows(rows, opt, true).map((r) => r.n)).toEqual(["a", "d", "c", "b"]);
    expect(sortRows(rows, undefined, false)).toBe(rows);
  });
});

function trade(over: Partial<ManageTrade>): ManageTrade {
  return {
    tradeId: "X",
    pnlNet: 0,
    capture: 0,
    nearestShortDte: 30,
    action: "HOLD",
    profile: { downsideFromNow: -100 },
    ...over,
  } as unknown as ManageTrade;
}

describe("the positions board's sort", () => {
  const trades = [
    trade({ tradeId: "AAA-1", pnlNet: 50, nearestShortDte: 40, capture: 0.1, profile: { downsideFromNow: -300 } } as Partial<ManageTrade>),
    trade({ tradeId: "BBB-1", pnlNet: -20, nearestShortDte: 5, capture: 0.6, action: "CLOSE", profile: { downsideFromNow: -50 } } as Partial<ManageTrade>),
    trade({ tradeId: "CCC-1", pnlNet: 120, nearestShortDte: 20, capture: 0.3, profile: { downsideFromNow: -900 } } as Partial<ManageTrade>),
  ];
  const by = (id: string, reversed = false) => sortRows(trades, POSITION_SORTS.find((s) => s.id === id), reversed).map((t) => t.tradeId);

  it("sorts by P&L high to low by default, and by timing, capture, action and downside on choice", () => {
    expect(by("pnl")).toEqual(["CCC-1", "AAA-1", "BBB-1"]);
    expect(by("dte")).toEqual(["BBB-1", "CCC-1", "AAA-1"]);
    expect(by("dte", true)).toEqual(["AAA-1", "CCC-1", "BBB-1"]);
    expect(by("capture")).toEqual(["BBB-1", "CCC-1", "AAA-1"]);
    expect(by("action")[0]).toBe("BBB-1");
    expect(by("downside")).toEqual(["CCC-1", "AAA-1", "BBB-1"]);
  });
  it("offers no sort by credit or yield", () => {
    for (const s of [...POSITION_SORTS, ...INCOME_SORTS]) expect(s.label).not.toMatch(/credit|yield|premium/i);
  });
});

function candidate(over: Partial<ManageCandidate>): ManageCandidate {
  return {
    underlying: "AAA",
    kind: "put_spread",
    alternativeTo: null,
    expiry: "2026-12-18",
    dte: 42,
    legs: [
      { strike: 50, right: "P", quantity: -1, bid: 1, ask: 1.1, delta: -0.2 },
      { strike: 45, right: "P", quantity: 1, bid: 0.4, ask: 0.5, delta: -0.1 },
    ],
    units: 1,
    binding: "per-trade risk",
    perUnit: { credit: 0.6, natural: 0.5, cash: 60, maxLoss: 440, buyingPower: 440, stressLoss: 440, theta: 1, bwd: 1 },
    thetaPerBp: null,
    earningsDate: null,
    earningsUnknown: false,
    alternative: null,
    ifRoom: null,
    leveraged: null,
    heldSameExpiry: false,
    odds: null,
    ...over,
  };
}

function income(fits: ManageCandidate[]): ManageCandidates {
  return {
    sourceSession: "2026-11-06",
    takenAt: null,
    accountBasis: "cash",
    names: 10,
    excluded: {},
    fits,
    fitsTotal: fits.length,
    tooBig: [],
    tooBigTotal: 0,
    waiting: [],
    waitingTotal: 0,
    bpUsedPct: 0.3,
    bpCapPct: 0.35,
    bpWarnOver: 0.1,
    error: null,
    caps: { perTradeRiskPct: 0.03, notionalPerNamePct: 0.25, maxNameThetaShare: 0.25 },
    exEarnings: null,
  };
}

describe("adding trades' filter and sort", () => {
  beforeEach(() => {
    void writePref("minPremium.live-income", 0);
    void writePref("sort.adding-income", undefined);
  });
  const c = income([
    candidate({ underlying: "BBB", dte: 30, perUnit: { ...candidate({}).perUnit, stressLoss: 200 } }),
    candidate({ underlying: "AAA", dte: 50, perUnit: { ...candidate({}).perUnit, stressLoss: 300 } }),
    candidate({ underlying: "CCC", dte: 40, perUnit: { ...candidate({}).perUnit, stressLoss: 100 } }),
  ]);
  const syms = (html: string) => [...html.matchAll(/class="pos-card-sym">([A-Z]+)</g)].map((m) => m[1]);

  it("lists by symbol by default and says what is on in the summary line", () => {
    const html = renderToString(<AddingTradesCard c={c} t={null} netLiq={10000} />);
    expect(syms(html)).toEqual(["AAA", "BBB", "CCC"]);
    const t = text(html);
    expect(t).toContain("any premium");
    expect(t).toContain("by symbol");
    expect(t).toContain("none hidden");
    // A floor away from its $50 default counts as one change.
    expect(html).toContain('aria-label="Filter and sort, 1 changed"');
  });

  it("sorts by the remembered choice", () => {
    void writePref("sort.adding-income", { id: "maxloss", reversed: false });
    expect(syms(renderToString(<AddingTradesCard c={c} t={null} netLiq={10000} />))).toEqual(["AAA", "BBB", "CCC"]);
    void writePref("sort.adding-income", { id: "expiry", reversed: false });
    const html = renderToString(<AddingTradesCard c={c} t={null} netLiq={10000} />);
    expect(syms(html)).toEqual(["BBB", "CCC", "AAA"]);
    expect(text(html)).toContain("by expiry");
  });
});
