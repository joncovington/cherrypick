import { afterEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { LiveFliesPayload, LiveMeicOpenRow, LiveMeicPayload, LivePosition, LiveTodayHandOrder, ManagePayload, MeicLiveGapPayload } from "@console/shared";
import { ListViewContext, useViewChoice } from "../src/components/ViewSwitch";
import { writePref } from "../src/lib/prefs";
import { LivePage, positionModel } from "../src/pages/Live/LivePage";
import { ALL_ACCOUNTS, PositionsPage, notEvaluatedWords, pickAccount, rulesetBox } from "../src/pages/Live/ManagePage";
import { MeicLivePage, icLineFor, keptShare, openModel, stopWords } from "../src/pages/Live/MeicLivePage";
import { MeicReadinessCard } from "../src/pages/Live/MeicReadinessCard";
import { handModel } from "../src/pages/Live/HandToday";
import { breakerShare } from "../src/pages/Live/LiveTodayPage";
import { TradeCard } from "../src/pages/Live/TradeCard";

/**
 * The Live tabs redesigned to match the portfolio tab (2026-11-06): today, positions, meic and flies.
 * Pinned here: what moved behind a ? or into a lightbox, the open condors defaulting to rows, the stop
 * said once, the live-vs-paper flag, the state lines, and the ruleset leaving the page.
 */

const text = (html: string) => html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'").replace(/&quot;/g, '"');

const MADE_UP_IC = "LIVE-XYZ-1 XYZ 100p/120c 10w, credit 1.8, holds call, stop 2.0x credit";

const openRow = (over: Partial<LiveMeicOpenRow> = {}): LiveMeicOpenRow => ({
  id: 1,
  tradeDate: "2026-11-06",
  entryTime: "2026-11-06T12:30:00",
  symbol: "XYZ",
  arm: "arm-a",
  status: "open",
  working: false,
  putStrike: 100,
  callStrike: 120,
  wingWidth: 10,
  quantity: 1,
  netCredit: 1.8,
  entryCash: 180,
  putHeld: true,
  callHeld: true,
  putClosing: false,
  callClosing: false,
  fillConfirmedAt: "2026-11-06T12:30:20",
  overdue: false,
  mark: null,
  ...over,
});

function meicPayload(over: Partial<LiveMeicPayload> = {}): LiveMeicPayload {
  return {
    generatedAt: "2026-11-06T17:00:00Z",
    session: "2026-11-06",
    ledger: "ok",
    ledgerError: null,
    arm: { armed: true, date: "2026-11-06", at: null, stale: false, disarmTime: "15:45", pastDisarm: false, halted: false, liveEnabled: true, symbol: "XYZ" },
    orphans: { at: null, orders: [] },
    held: { unresolved: [], unrecorded: [] },
    brokerHealth: null,
    open: [openRow()],
    history: [],
    totals: { trades: 0, sessions: 0, gross: 0, fees: 0, settlementFees: 0, settlementRecorded: 0, settlementTrades: 0, net: 0, feesTrades: 0, feesRecorded: 0, netRecorded: 0 },
    readiness: { ok: true, ready: true, blockers: [], warnings: [], openIcs: [MADE_UP_IC], criticals: [] },
    ...over,
  };
}

const gapPayload: MeicLiveGapPayload = {
  twin: "live-shadow",
  ledger: "ok",
  since: "2026-11-01",
  rows: [],
  totals: { paired: 0, liveNet: 0, paperNet: 0, gap: 0, paperOnly: 0, paperOnlyNet: 0, liveOnly: 0 },
};

function renderIn(ui: JSX.Element, seed: Array<[readonly unknown[], unknown]>): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const [k, v] of seed) qc.setQueryData(k, v);
  return text(
    renderToString(
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

afterEach(() => {
  void writePref("view.live-meic-open", undefined);
});

describe("Live → meic", () => {
  const page = () => renderIn(<MeicLivePage />, [[["live-meic", "main"], meicPayload()], [["live-meic-gap"], gapPayload]]);

  it("leads with readiness, then what is open, then the record", () => {
    const html = page();
    const ready = html.indexOf("Can it trade today?");
    const open = html.indexOf("What is open?");
    const record = html.indexOf("How has it done?");
    expect(ready).toBeGreaterThan(-1);
    expect(ready).toBeLessThan(open);
    expect(open).toBeLessThan(record);
    expect(html.indexOf("Ready to arm")).toBeLessThan(open);
    // the arm, halt and gate tiles sit in the readiness card, not a card of their own
    expect(html.indexOf("halt flag")).toBeLessThan(open);
  });

  it("draws the open condors as rows unless the reader chose cards", () => {
    const html = page();
    expect(html).toContain('class="tr-row"');
    expect(html).not.toContain("tc-card");
  });

  it("says the stop once, in the card's foot, and not again in the readiness card", () => {
    const m = openModel(openRow(), [MADE_UP_IC]);
    expect(m.foot[1]).toBe("stop 2.0× credit");
    expect(renderToString(<TradeCard m={m} />)).toContain("stop 2.0× credit");
    const html = page();
    expect(html).not.toContain("holds call, stop");
    // the module's line for a condor the board does not show stays in the readiness card
    const other = renderIn(<MeicLivePage />, [[["live-meic", "main"], meicPayload({ open: [] })], [["live-meic-gap"], gapPayload]]);
    expect(other).toContain(MADE_UP_IC);
  });

  it("matches the module's line to a condor by its short strikes, and reads the stop from it", () => {
    expect(icLineFor(openRow(), [MADE_UP_IC])).toBe(MADE_UP_IC);
    expect(icLineFor(openRow({ putStrike: 90 }), [MADE_UP_IC])).toBeNull();
    expect(stopWords(MADE_UP_IC)).toBe("stop 2.0× credit");
    expect(stopWords("LIVE-XYZ-1 XYZ 100p/120c 10w, credit 1.8")).toBeNull();
  });

  it("has no bar for the credit kept without a mark, and none is made up", () => {
    expect(openModel(openRow()).hideBar).toBe(true);
    expect(renderToString(<TradeCard m={openModel(openRow())} />)).not.toContain("pos-bar");
  });

  it("draws the credit kept against the entry credit from the mark, and prints each leg at its mid", () => {
    const mark = { at: "2026-11-06T17:45:00.000Z", mids: { shortPut: 0.5, longPut: 0.1, shortCall: 0.35, longCall: 0.05 }, closeCost: 0.7, exitCash: -70 };
    const r = openRow({ mark });
    // entry +$180, close at the mark −$70: $110 kept, 61% of the credit.
    expect(keptShare(r)).toEqual({ dollars: 110, share: (110 / 180) * 100 });
    const m = openModel(r);
    expect(m.hideBar).toBe(false);
    expect(m.label).toBe("61% of the credit kept · close 0.70");
    expect(m.legs.map((l) => l.text)).toEqual(["1 100P @ 0.50", "1 90P @ 0.10", "1 120C @ 0.35", "1 130C @ 0.05"]);
    expect(text(renderToString(<TradeCard m={m} />))).toContain("pos-bar");
    // A mark past the credit keeps nothing: the bar is empty, never negative.
    expect(keptShare(openRow({ mark: { ...mark, closeCost: 2.4, exitCash: -240 } }))).toEqual({ dollars: -60, share: 0 });
  });

  it("puts the history's net rule behind its ?, and its totals in chips that wrap inside the card", () => {
    const totals = { trades: 3, sessions: 2, gross: 300, fees: 12, settlementFees: 1.5, settlementRecorded: 1.5, settlementTrades: 3, net: 286.5, feesTrades: 3, feesRecorded: 12, netRecorded: 286.5 };
    const html = renderIn(<MeicLivePage />, [[["live-meic", "main"], meicPayload({ totals })], [["live-meic-gap"], gapPayload]]);
    expect(html).toContain('aria-label="About History — 0 resolved live"');
    expect(html).not.toContain("Gross less fees less settlement");
    const foot = html.slice(html.indexOf('class="lv-totals"'));
    expect(foot.indexOf('class="lv-totals"')).toBe(0);
    for (const part of [">3 trades · 2 sessions<", ">gross $300.00<", ">settlement $1.50<"]) expect(foot).toContain(part);
  });

  it("flags live vs paper as paired, and opens execution from it as a lightbox, not a table on the page", () => {
    const html = page();
    expect(html).toContain("paired, not pooled");
    expect(html).toContain("execution by week");
    expect(html).not.toContain("Slip vs mid");
    expect(html).not.toContain("Overrides");
  });

  it("still says readiness is unknown when the bridge failed, never ready", () => {
    const html = renderIn(<MeicLivePage />, [[["live-meic", "main"], meicPayload({ readiness: { ok: false, error: "status unavailable" } })], [["live-meic-gap"], gapPayload]]);
    expect(html).toContain("Readiness unknown");
    expect(html).not.toContain("Ready to arm");
  });
});

describe("the readiness card", () => {
  it("lists a held condor only where the board does not already show it", () => {
    const readiness = { ok: true as const, ready: true, blockers: [], warnings: [], openIcs: [MADE_UP_IC], criticals: [] };
    const all = text(renderToString(<MeicReadinessCard readiness={readiness} />));
    expect(all).toContain(MADE_UP_IC);
    const shown = text(renderToString(<MeicReadinessCard readiness={readiness} shown={() => true} />));
    expect(shown).not.toContain(MADE_UP_IC);
  });
});

describe("cards or rows, with a default", () => {
  function Probe({ fallback }: { fallback?: "rows" }) {
    const [v] = useViewChoice("probe", fallback);
    return <span>{v}</span>;
  }
  it("falls back to cards, or to the rows a list asked for, until one is chosen", () => {
    expect(renderToString(<Probe />)).toContain(">cards<");
    expect(renderToString(<Probe fallback="rows" />)).toContain(">rows<");
    expect(renderToString(<ListViewContext.Provider value="rows"><Probe /></ListViewContext.Provider>)).toContain(">cards<");
  });
});

describe("Live → positions", () => {
  const manage = (over: Partial<ManagePayload> = {}): ManagePayload =>
    ({
      session: "2026-11-06",
      sessions: ["2026-11-06"],
      snapshotGeneratedAt: "2026-11-06T15:30:00-05:00",
      ok: true,
      error: null,
      metricsError: null,
      transactionsError: null,
      ruleset: "manage-test",
      rulesSource: "shipped default",
      ruleWarnings: [],
      rules: { risk: { oversize_multiple: 2 }, take_profit: [0.5] },
      accounts: [{ account: "****1234", netLiq: 100000, maintenance: null, buyingPower: null, riskBudget: 3000, pnlGross: 0, counts: {}, trades: [], excluded: [{ underlying: "ABC", legs: 4, reason: "0DTE left out by the ruleset" }], portfolio: null, wheel: null, pmcc: null }],
      snapshotStale: null,
      preOpen: null,
      early: null,
      ...over,
    }) as unknown as ManagePayload;
  const page = (m: ManagePayload) => renderIn(<PositionsPage />, [[["manage", "latest"], m]]);

  it("keeps the ruleset out of the page: a button opens it as a lightbox", () => {
    const html = page(manage());
    expect(html).not.toContain("oversize_multiple");
    expect(html).not.toContain("<pre");
    expect(html).toMatch(/<button[^>]*class="pf-more"[^>]*>ruleset/);
    expect(html).toContain("Is the reading current?");
    expect(html).toContain("What does each trade need?");
  });

  it("says what was not evaluated as an amber state line under the board", () => {
    const html = page(manage());
    expect(html).toMatch(/class="lv-state is-warn"[^>]*>not evaluated: ABC \(4 legs, 0DTE left out by the ruleset\)</);
    expect(html.indexOf("No trades in this account.")).toBeLessThan(html.indexOf("not evaluated: ABC"));
    expect(notEvaluatedWords([])).toBeNull();
  });

  it("keeps the snapshot and ruleset line out of the head, naming only a default ruleset", () => {
    expect(page(manage({ rulesSource: "/x/manage.json" }))).not.toContain("· snapshot");
    expect(page(manage({ rulesSource: "/x/manage.json" }))).not.toContain("/x/manage.json");
    expect(page(manage({ rulesSource: "/x/manage.json" }))).not.toContain("the shipped default");
    expect(page(manage({ rulesSource: null }))).toContain("ruleset manage-test: the shipped default");
  });

  it("moves the method line behind the header's ?", () => {
    const html = page(manage());
    expect(html).not.toContain("advice only: marks are mids");
    expect(html).toContain("About position evaluation");
  });

  it("the ruleset lightbox lists each setting, then the raw JSON", () => {
    const box = rulesetBox("manage-test", { risk: { oversize_multiple: 2 }, take_profit: [0.5] });
    expect(box.tabs.map((t) => t.id)).toEqual(["rules", "raw"]);
    const rules = text(renderToString(<>{box.body("rules")}</>));
    expect(rules).toContain("risk.oversize_multiple");
    expect(rules).toContain("take_profit");
    expect(text(renderToString(<>{box.body("raw")}</>))).toContain('"oversize_multiple": 2');
  });

  const twoAccounts = () => {
    const base = manage().accounts[0]!;
    return manage({
      accounts: [
        { ...base, account: "****1234", netLiq: 100000 },
        { ...base, account: "other ****7777", loginLabel: "Other", netLiq: 5000 },
      ],
    } as Partial<ManagePayload>);
  };
  const pageAt = (url: string, m: ManagePayload) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["manage", "latest"], m);
    return text(
      renderToString(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={[url]}>
            <PositionsPage />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
  };

  it("draws one account at a time, the first unless the select names another", () => {
    const first = pageAt("/live/positions", twoAccounts());
    expect(first).toContain('aria-label="account"');
    expect(first).toContain("net liq · ****1234");
    expect(first).not.toContain("net liq · other ****7777");
    const second = pageAt("/live/positions?acct=other%20****7777", twoAccounts());
    expect(second).toContain("net liq · other ****7777 (Other)");
    expect(second).not.toContain("net liq · ****1234");
    const all = pageAt("/live/positions?acct=all", twoAccounts());
    expect(all).toContain("net liq · ****1234");
    expect(all).toContain("net liq · other ****7777");
  });

  it("draws no account select with one account", () => {
    expect(pageAt("/live/positions?acct=gone", manage())).not.toContain('aria-label="account"');
    expect(pageAt("/live/positions?acct=gone", manage())).toContain("net liq · ****1234");
  });

  it("picks the named account, all, or the first when the name is unknown", () => {
    expect(pickAccount([], "x")).toBeNull();
    expect(pickAccount(["a"], "b")).toBe("a");
    expect(pickAccount(["a", "b"], null)).toBe("a");
    expect(pickAccount(["a", "b"], "b")).toBe("b");
    expect(pickAccount(["a", "b"], "gone")).toBe("a");
    expect(pickAccount(["a", "b"], ALL_ACCOUNTS)).toBe(ALL_ACCOUNTS);
  });
});

describe("Live → today", () => {
  it("reads a loop's breaker as a share of its limit, and nothing while there is none", () => {
    expect(breakerShare({ limit: 500, figure: -250, figureKind: "net", tripped: false, basis: "" })).toBe(50);
    expect(breakerShare({ limit: 500, figure: 40, figureKind: "net", tripped: false, basis: "" })).toBe(0);
    expect(breakerShare({ limit: null, figure: -10, figureKind: "net", tripped: null, basis: "" })).toBeNull();
    expect(breakerShare({ limit: 500, figure: null, figureKind: "net", tripped: null, basis: "" })).toBeNull();
  });

  it("draws a hand order as a card with its closing legs tagged BTC / STC and no bar", () => {
    const o: LiveTodayHandOrder = {
      orderId: "9",
      at: "2026-11-06T16:00:00Z",
      kind: "close",
      legs: [
        { symbol: "XYZW  261106P00100000", action: "Buy to Close", quantity: 1, price: 0.5, value: -50 },
        { symbol: "XYZW  261106P00090000", action: "Sell to Close", quantity: 1, price: 0.1, value: 10 },
      ],
      cash: -40,
      fees: 1.3,
    };
    const m = handModel(o, "XYZ");
    expect(m.hideBar).toBe(true);
    expect(m.open.role).toBe("close");
    const html = text(renderToString(<TradeCard m={m} />));
    expect(html).toContain(">BTC<");
    expect(html).toContain(">STC<");
    expect(html).not.toContain("BTO");
    expect(html).not.toContain("pos-bar");
  });
});

describe("Live → flies", () => {
  const position = (over: Partial<LivePosition> = {}): LivePosition => ({
    positionId: "p1",
    kind: "fly",
    side: "put",
    center: 100,
    wingWidth: 10,
    quantity: 1,
    netCredit: 1.1,
    fees: 1,
    floorDollars: 0,
    riskFree: true,
    status: "open",
    entryTime: "2026-11-06T10:42:00",
    entryFillStatus: "filled",
    completionFillStatus: "filled",
    completedAt: "2026-11-06T10:58:00",
    mark: { at: "2026-11-06T14:00:00", structureMid: 1, markPnl: 42, restingLimit: null },
    pnl: null,
    ...over,
  });

  it("draws a fly as a trade card: body twice, wings once, the credit and the mark", () => {
    const m = positionModel(position(), "XYZ");
    expect(m.legs.map((l) => `${l.buy ? "BTO" : "STO"} ${l.text}`)).toEqual(["BTO 1 90P", "STO 2 100P", "BTO 1 110P"]);
    expect(m.label).toBe("risk-free");
    expect(m.after[0]).toContain("mark $42.00");
    expect(positionModel(position({ kind: "short_vertical", side: "call" }), "XYZ").legs.map((l) => l.text)).toEqual(["1 100C", "1 110C"]);
  });

  it("holds the ratio caveat behind a flag on performance and the long tables in a lightbox", () => {
    const perf = { tiles: { netPnl: 0, trades: 0, sessions: 0, winRatePct: null, profitFactor: null, feeDragPct: null, completionRatePct: null }, risk: { sharpe: null, sortino: null, calmar: null, recoveryFactor: null, sampleSize: 3, sharpeOverfitFlag: false }, equity: [], sessions: [], completion: {}, completionTrend: [], liveVsPaper: null, calibration: { reading: null, error: null, from: null }, onRisk: { ratio: null, net: 0, peakRisk: 0, sessions: 0, of: 0 }, maxDrawdown: null, arm: null } as never;
    const periods = { trades: 0, net: 0, sessions: 0, paperNet: null, paperSessions: null, onRisk: { ratio: null, net: 0, peakRisk: 0, sessions: 0, of: 0 } };
    const d = {
      session: "2026-11-06",
      sessionBasis: "today",
      moduleEnabled: false,
      ledger: "ok",
      ledgerError: null,
      arm: { armed: false, date: null, stale: false, halted: false, liveEnabled: false, arm: "control", symbol: "XYZ" },
      periods: { today: periods, week: periods, month: periods, year: periods },
      today: { open: 0, pending: 0, markPnl: null, markedAt: null, maxDrawdown: null, completionPct: null, sessionPeakWorst: null },
      buyingPower: null,
      series: { spx: [], vix: [], vix3m: [], markPnl: [], openMargin: [], gaps: [] },
      positions: [position()],
      feed: [],
      performance: perf,
    } as unknown as LiveFliesPayload;
    const html = renderIn(<LivePage />, [[["live-flies"], d]]);
    expect(html).toContain("few sessions");
    expect(html).not.toContain("Ratios annualized");
    expect(html).toContain("module off: flies is switched off");
    expect(html).toContain("tr-row");
    expect(html).toContain("activity");
    expect(html).toContain("by session, completion and calibration");
    expect(html).not.toContain("net / peak risk");
  });
});
