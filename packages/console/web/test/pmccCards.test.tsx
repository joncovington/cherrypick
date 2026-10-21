import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageAccount, ManagePmcc, ManagePmccPosition, ManageTrade } from "@console/shared";
import { NO_PMCC, PmccCards } from "../src/pages/Live/PmccCards";
import { aligned, inView, tables } from "./viewTables";

/**
 * Live → pmcc draws packages/manage's PMCC view and decides nothing. The guards: an account with no
 * PMCC says how one is recognised (and why there is no view, when there is an error); an unreconciled
 * PMCC lists its problems and shows no rule; an unknown delta at entry says so; and the width check
 * and the assignment exposure are manage's flags, never worked out from the figures beside them.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

/** The PMCC packages/manage's own test (test_pmcc) evaluates: a 45 short over a 40 long whose net
 *  cost is 7.62 a share, so the width fails, with one quoted roll. As the reader shapes it. */
function position(over: Partial<ManagePmccPosition> = {}): ManagePmccPosition {
  return {
    underlying: "XYZ",
    recognized: true,
    recognizedBy: "entered at 0.82 delta (at least 0.80)",
    intent: null,
    reconciled: true,
    problems: [],
    spot: 43,
    long: {
      symbol: "XYZ 280121C00040000",
      leg: "2028-01-21 40C",
      strike: 40,
      expiration: "2028-01-21",
      dte: 465,
      quantity: 1,
      mid: 8,
      opened: "2026-04-01",
      daysAtEntry: 660,
      deltaAtEntry: 0.8248,
      deltaAtEntryWhy: null,
      entryFills: [],
      delta: 0.7075,
      intrinsic: 3,
      extrinsic: 5,
      extrinsicShare: 0.625,
      roll: [],
      rollDeltaUnknown: false,
    },
    short: {
      symbol: "XYZ 261120C00045000",
      leg: "2026-11-20 45C",
      strike: 45,
      expiration: "2026-11-20",
      dte: 38,
      quantity: -1,
      mid: 0.8,
      openPrice: 6,
      tested: false,
      intrinsic: 0,
      extrinsic: 0.8,
      probItm: 0.3038,
      band: null,
      bandMissing: "band not in the snapshot",
      beyondBand: null,
      assignment: { nearZero: false, dividend: false, exposed: false, why: null, exDividendDate: null, dividendAmount: null },
      earnings: { date: null, inside: null, why: "no report date in the snapshot" },
    },
    width: {
      width: 5,
      netCost: 762,
      netCostPerShare: 7.62,
      basis: "linked orders",
      passes: false,
      pnlIfAssigned: -262,
      pnlIfAssignedNet: -265.3,
      assumes: "the long exercised to deliver the shares; selling it instead also keeps its time value",
    },
    shorts: [
      { symbol: "XYZ 260515C00055000", leg: "2026-05-15 55C", sold: "2026-04-01", soldPrice: 1.5, cash: 150, fees: -1.1, status: "expiration" },
      { symbol: "XYZ 261120C00045000", leg: "2026-11-20 45C", sold: "2026-09-25", soldPrice: 6, cash: 600, fees: -1.1, status: "open" },
    ],
    orders: ["1", "2"],
    netCash: -762,
    fees: -3.3,
    longCost: 1512,
    premiums: 750,
    skateYield: { value: 0.829, capped: false, basis: "comparison figure: the shorts' P&L to date / the long's cost x 365 / days held" },
    rolls: {
      candidates: [
        {
          expiration: "2026-12-18",
          open: { strike: 48, bid: 0.75, ask: 0.85, mid: 0.8 },
          delta: 0.246,
          closePrice: 0.8,
          net: 0,
          legs: ["BTC 1 2026-11-20 45C @ 0.80", "STO 1 2026-12-18 48C @ 0.80 (quoted 0.75/0.85)"],
          check: { verdict: "ok", credit: 0, maxLossBefore: 762, maxLossAfter: 762, shapeBefore: "diagonal", shapeAfter: "diagonal", problems: [], notes: [], after: [] },
          checkError: null,
        },
      ],
      why: null,
      quotedExpiries: ["2026-12-18"],
      quotedAt: "2026-10-12T18:45:00Z",
      chainsTakenAt: null,
    },
    tradeIds: ["XYZ#2"],
    ...over,
  };
}

function trade(over: Partial<ManageTrade> = {}): ManageTrade {
  return {
    tradeId: "XYZ#2",
    underlying: "XYZ",
    spot: 43,
    kind: "credit",
    shape: "diagonal",
    intent: null,
    note: null,
    cost: null,
    value: null,
    pnlGross: null,
    pnlNet: null,
    fees: null,
    basis: "order_chain",
    chain: null,
    capture: null,
    nearestShortExpiry: "2026-11-20",
    nearestShortDte: 38,
    action: "ROLL_OR_CLOSE",
    confidence: 65,
    rule: "pmcc.width",
    reason: "",
    explanation: null,
    agreeing: [],
    conflicts: [],
    replaces: [],
    roll: null,
    fired: [
      { rule: "pmcc.width", action: "ROLL_OR_CLOSE", confidence: 65, reason: "the 2026-11-20 45C is 5 over the 2028-01-21 40C: an assignment there loses $262", legs: null },
      { rule: "credit.take_profit", action: "TAKE_PROFIT", confidence: 70, reason: "not a PMCC rule", legs: null },
    ],
    legs: [],
    opening: null,
    order: null,
    takeProfit: null,
    stop: null,
    timeExit: null,
    earnings: null,
    exDividend: null,
    profile: null,
    earningsTrade: null,
    heldForEarnings: [],
    wheel: null,
    pmcc: null,
    ...over,
  };
}

function view(over: Partial<ManagePmcc> = {}): ManagePmcc {
  return {
    positions: [position()],
    underlyingHistoryError: null,
    bandsMissing: "band not in the snapshot",
    error: null,
    recognize: { entryMinDelta: 0.8, minLongDays: 180, maxShortDays: 60 },
    ...over,
  };
}

function account(v: ManagePmcc | null, trades: ManageTrade[] = [trade()]): ManageAccount {
  return {
    account: "****1234",
    netLiq: 100000,
    maintenance: null,
    buyingPower: null,
    riskBudget: null,
    pnlGross: null,
    counts: {},
    trades,
    excluded: [],
    portfolio: null,
    wheel: null,
    pmcc: v,
  };
}

function render(a: ManageAccount): string {
  return text(renderToString(<PmccCards a={a} />));
}

describe("the pmcc tab", () => {
  it("says how a PMCC is recognised when there is none, and why when the view has an error", () => {
    const empty = render(account(view({ positions: [] })));
    expect(empty).toContain(NO_PMCC);
    expect(empty).toContain("0.80 delta or more");
    expect(empty).toContain('intent: "pmcc"');
    expect(empty).not.toContain("no PMCC view for this account");
    const failed = render(account(view({ positions: [], error: "no trade history in this snapshot" })));
    expect(failed).toContain("no PMCC view for this account: no trade history in this snapshot");
  });

  it("an evaluation from before the PMCC view says so, rather than claiming there is no PMCC", () => {
    const out = render(account(null));
    expect(out).toContain("written before the PMCC view");
    expect(out).not.toContain(NO_PMCC);
  });

  it("a recognised PMCC shows the long, the width check, the shorts sold, the yield and its rules", () => {
    const out = render(account(view()));
    expect(out).toContain("XYZ · PMCC 2028-01-21 40C / 2026-11-20 45C");
    expect(out).toContain("delta at entry 0.82");
    expect(out).toContain("delta today 0.71");
    expect(out).toContain("extrinsic 5.00 a share (63% of its price)");
    expect(out).toContain("days left 465");
    expect(out).toContain("no roll due");
    expect(out).toContain("strike width 5.00 a share");
    expect(out).toContain("long's net cost after premiums $762.00 (7.62 a share)");
    expect(out).toContain("P&L if assigned, gross -$262.00");
    expect(out).toContain("P&L if assigned, net of fees -$265.30");
    expect(out).toContain("premiums, net +$750.00");
    expect(out).toContain("fees paid $3.30");
    expect(out).toContain("comparison figure");
    expect(out).toContain("82.9%");
    // Today's check on the short, the wheel's columns and the PMCC's own.
    expect(out).toContain("band not in the snapshot");
    expect(out).not.toContain("band not in the snapshot (band not in the snapshot)");
    expect(out).toContain("30%");
    expect(out).toContain("no report date in the snapshot");
    // Every short sold against the long.
    expect(out).toContain("2026-05-15 55C 2026-04-01 1.50 +$150.00 $1.10 expiration");
    // Only the PMCC rules, with the roll and its verdict.
    expect(out).toContain("pmcc.width");
    expect(out).not.toContain("credit.take_profit");
    expect(out).toContain("2026-12-18 48C at 0.25 delta");
    expect(out).toContain("check-roll ok");
  });

  it("an unknown delta at entry says so, never a number", () => {
    const p = position({ recognized: false, recognizedBy: "delta at entry unknown (no close for 2026-04-01 in the snapshot)" });
    p.long = { ...p.long, deltaAtEntry: null, deltaAtEntryWhy: "no close for 2026-04-01 in the snapshot" };
    const out = render(account(view({ positions: [p] })));
    expect(out).toContain("delta at entry unknown (no close for 2026-04-01 in the snapshot)");
    expect(out).toContain("not recognised as a PMCC: its diagonal rules stand");
    expect(out).not.toContain("pmcc.width");
  });

  it("an unreconciled PMCC lists its problems and shows no rule", () => {
    const p = position({ reconciled: false, problems: ["the long's opening predates the history"], rolls: null });
    p.width = { ...p.width, basis: "open_legs", pnlIfAssignedNet: null };
    const t = trade({ fired: [{ rule: "pmcc_data", action: "REVIEW", confidence: null, reason: "the PMCC's orders do not reconcile", legs: null }] });
    const out = render(account(view({ positions: [p] }), [t]));
    expect(out).toContain("unreconciled");
    expect(out).toContain("the long's opening predates the history");
    expect(out).toContain("no PMCC rule judged");
    expect(out).toContain("n/r: the orders do not reconcile");
    expect(out).not.toContain("pmcc_data");
    expect(out).not.toContain("pmcc.width");
    expect(out).not.toContain("check-roll");
  });

  it("the width check and the assignment exposure are manage's flags, never worked out from the figures", () => {
    // 5 of width against a 7.62 net cost fails by arithmetic; manage's flag says it passes.
    const p = position();
    p.width = { ...p.width, passes: true };
    // In the money with a cent of time value is exposed by arithmetic; manage's flag says not.
    p.short = { ...p.short, intrinsic: 2, extrinsic: 0.01, assignment: { ...p.short.assignment, exposed: false } };
    const out = render(account(view({ positions: [p] })));
    expect(out).toMatch(/width check passes /);
    expect(out).not.toMatch(/width check fails /);
    expect(out).toMatch(/ not exposed /);

    // And the other way: a width that passes by arithmetic, flagged as failing; an out-of-the-money
    // short with plenty of time value, flagged as exposed.
    const q = position();
    q.width = { ...q.width, width: 10, netCostPerShare: 2, passes: false };
    q.short = { ...q.short, intrinsic: 0, extrinsic: 3, assignment: { ...q.short.assignment, exposed: true, why: "in the money over the ex-date" } };
    const out2 = render(account(view({ positions: [q] })));
    expect(out2).toMatch(/width check fails /);
    expect(out2).toMatch(/ exposed in the money over the ex-date/);
    expect(out2).not.toMatch(/ not exposed /);
  });

  it("names every recognition limit from the evaluation's own ruleset, and no number without one", () => {
    const empty = render(account(view({ positions: [], recognize: { entryMinDelta: 0.8, minLongDays: 180, maxShortDays: 60 } })));
    expect(empty).toContain("at least 180 days left when it was bought");
    expect(empty).toContain("at most 60 days left");
    const tuned = render(account(view({ positions: [], recognize: { entryMinDelta: 0.75, minLongDays: 200, maxShortDays: 45 } })));
    expect(tuned).toContain("0.75 delta or more");
    expect(tuned).toContain("at least 200 days");
    expect(tuned).toContain("at most 45 days");
    const none = render(account(view({ positions: [], recognize: null })));
    expect(none).toContain(NO_PMCC);
    expect(none).not.toMatch(/\d+ days/);
  });

  it("says whose quotes the rolls are priced on, in ET", () => {
    const income = render(account(view()));
    expect(income).toContain("priced on the income reading (taken 2026-10-12 14:45 ET)");
    const p = position();
    p.rolls = { ...p.rolls!, chainsTakenAt: "2026-10-21T14:35:47-0600" };
    const chains = render(account(view({ positions: [p] })));
    expect(chains).toContain(
      "priced on this session's roll-out chains (read 2026-10-21 16:35 ET), and the income reading (taken 2026-10-12 14:45 ET) for any expiry they lack",
    );
  });

  it("labels an older evaluation's gross skate yield as gross, and states a net one as manage wrote it", () => {
    const old = render(account(view()));
    expect(old).toContain("(gross: the shorts' fees not subtracted)");
    const p = position({
      skateYield: {
        value: 0.81,
        capped: false,
        basis: "comparison figure: the shorts' net P&L to date (their fees subtracted) / the long's cost x 365 / days held",
      },
    });
    const now = render(account(view({ positions: [p] })));
    expect(now).toContain("the shorts' net P&L to date (their fees subtracted)");
    expect(now).not.toContain("(gross:");
  });

  it("labels the check's underlying column as the facts card does", () => {
    const html = renderToString(<PmccCards a={account(view())} />);
    expect(html).toContain("<th>underlying</th>");
    expect(html).not.toContain("<th>price</th>");
  });
});

/** Live → pmcc in the simple and detail views (2026-10-21). */
describe("the pmcc check, simple and detail", () => {
  const shape = (mode: "simple" | "detail") =>
    tables(inView(mode, () => renderToString(<PmccCards a={account(view())} />))).find((t) => t.headers.includes("assignment"))!;

  it("has no intrinsic column in either view, and keeps the band and N(d2) to detail", () => {
    const simple = shape("simple");
    const detail = shape("detail");
    expect(simple.headers).not.toContain("intrinsic (a share)");
    expect(detail.headers).not.toContain("intrinsic (a share)");
    for (const h of ["skate band", "strike vs band", "N(d2)"]) {
      expect(simple.headers).not.toContain(h);
      expect(detail.headers).toContain(h);
    }
    expect(simple.headers).toContain("underlying");
    expect(aligned(simple)).toBe(true);
    expect(aligned(detail)).toBe(true);
  });
});
