import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { LiveTodayHand, LiveTodayLoop, LiveTodayPayload } from "@console/shared";
import { LiveTodayView, suiteBadgeText } from "../src/pages/Live/LiveTodayPage";
import { aligned, inView, tables } from "./viewTables";

/**
 * The Live page's `today` tab. Pinned: a quiet day says plainly that nothing needs a person; an
 * attention item is listed with its loop; settlement never recorded reads n/r, never $0.00; a suite
 * total that could not be summed reads n/r; and the header badge says how many loops are armed and
 * whether the halt flag is up.
 */

function loop(id: LiveTodayLoop["loop"], over: Partial<LiveTodayLoop> = {}): LiveTodayLoop {
  return {
    loop: id,
    to: `/live/${id}`,
    ledger: "ok",
    ledgerError: null,
    arm: { armed: false, date: null, at: null, stale: false, disarmTime: "17:00", pastDisarm: false, liveEnabled: true, arm: null, symbol: "SPX" },
    loopState: null,
    open: 0,
    working: 0,
    workingBasis: "",
    worstCase: null,
    worstCaseBasis: "",
    entriesToday: 0,
    today: { trades: 0, gross: 0, fees: 0, settlementFees: 0, settlementRecorded: 0, settlementTrades: 0, net: 0, feesTrades: 0, netRecorded: 0, basis: "" },
    mark: null,
    breaker: { limit: null, figure: null, figureKind: "net", tripped: null, basis: "" },
    orphans: { at: null, count: 0 },
    held: 0,
    brokerHealth: null,
    overdue: [],
    moduleEnabled: true,
    retired: false,
    ...over,
  };
}

function payload(over: Partial<LiveTodayPayload> = {}): LiveTodayPayload {
  return {
    generatedAt: "2026-10-15T16:00:00Z",
    session: "2026-10-15",
    halted: false,
    loops: [loop("flies"), loop("meic"), loop("bwb")],
    attention: [],
    suite: { loops: 3, armed: [], net: 0, gross: 0, fees: 0, settlementFees: 0, trades: 0, summed: ["flies", "meic", "bwb"], excluded: [], feesUnrecorded: [] },
    hand: hand({ status: "none", reason: "no position evaluation today yet (newest 2026-10-14)" }),
    ...over,
  };
}

function hand(over: Partial<LiveTodayHand> = {}): LiveTodayHand {
  return {
    status: "ok",
    reason: null,
    evaluatedSession: "2026-10-15",
    snapshotAt: "2026-10-15T12:00:00-0400",
    markedAt: "2026-10-15T12:05:00-04:00",
    underlyings: ["SPX"],
    orders: [],
    openLegs: [],
    unreconciled: [],
    afterPublish: [],
    knownModules: { meic: "2026-10-15T12:04:00-04:00" },
    loopFills: 0,
    cash: 0,
    mark: 0,
    gross: 0,
    fees: 0,
    settlement: 0,
    net: 0,
    netBasis: "realised: nothing held",
    caveats: [],
    ...over,
  };
}

/** A hand iron condor still held: +$180 in, $2.88 fees, the four legs at a −$50 mark. */
const HAND_IC: Partial<LiveTodayHand> = {
  orders: [
    {
      orderId: "200",
      at: "2026-10-15T15:35:00Z",
      kind: "open",
      legs: [
        { symbol: "SPXW  261015P07555000", action: "Sell to Open", quantity: 1, price: 2.12, value: 212 },
        { symbol: "SPXW  261015P07545000", action: "Buy to Open", quantity: 1, price: 1.07, value: -107 },
        { symbol: "SPXW  261015C07590000", action: "Sell to Open", quantity: 1, price: 1.17, value: 117 },
        { symbol: "SPXW  261015C07600000", action: "Buy to Open", quantity: 1, price: 0.42, value: -42 },
      ],
      cash: 180,
      fees: 2.88,
    },
  ],
  openLegs: [
    { symbol: "SPXW  261015P07555000", right: "P", strike: 7555, quantity: -1, mid: 0.5, value: -50, markSource: "feed", markAgeSeconds: 1 },
  ],
  loopFills: 6,
  cash: 180,
  mark: -50,
  gross: 130,
  fees: 2.88,
  settlement: null,
  net: 127.12,
  netBasis: "at mark: held legs at mid, not yet settled",
};

const render = (d: LiveTodayPayload) =>
  renderToString(
    <MemoryRouter>
      <LiveTodayView data={d} />
    </MemoryRouter>,
  );

describe("LiveTodayView", () => {
  it("says plainly when nothing needs a person", () => {
    const html = render(payload());
    expect(html).toContain("Nothing needs a person");
    expect(html).toContain('href="/live/flies"');
    expect(html).toContain('href="/live/meic"');
  });

  it("folds a retired loop to one line and keeps its link", () => {
    const html = render(payload({ loops: [loop("flies", { moduleEnabled: false, retired: true }), loop("meic")] }));
    expect(html).toContain("off — module disabled, nothing open or working");
    expect(html).toContain('href="/live/flies"');
    expect(html.match(/not armed/g)?.length).toBe(1); // only meic's full row
  });

  it("lists an attention item under its loop", () => {
    const html = render(
      payload({ attention: [{ loop: "meic", kind: "orphaned_orders", detail: "1 working broker order no live ledger knows", to: "/live/meic" }] }),
    );
    expect(html).not.toContain("Nothing needs a person");
    expect(html).toContain("orphaned orders");
    expect(html).toContain("1 working broker order no live ledger knows");
  });

  it("shows settlement never recorded as n/r, and an unsummed suite total as n/r", () => {
    const flies = loop("flies", {
      today: { trades: 2, gross: 80, fees: 11, settlementFees: null, settlementRecorded: 5, settlementTrades: 1, net: 64, feesTrades: 2, netRecorded: 64, basis: "" },
    });
    const html = render(
      payload({
        loops: [flies, loop("meic"), loop("bwb", { ledger: "failed", ledgerError: "boom", today: null })],
        suite: { loops: 3, armed: [], net: null, gross: null, fees: null, settlementFees: null, trades: null, summed: [], excluded: [{ loop: "bwb", reason: "the live ledger could not be read" }], feesUnrecorded: [] },
      }),
    );
    expect(html).toContain("n/r (1 of 2)");
    expect(html).toContain("read failed");
    expect(html).toMatch(/net today · all loops[\s\S]*?n\/r/);
  });

  it("reads a trade's unrecorded fees as n/r, never gross less $0, and says why the suite net is n/r", () => {
    // Live row 6 (2026-06-29): gross −$2.00, fees never recorded.
    const meic = loop("meic", {
      today: { trades: 2, gross: 2, fees: null, settlementFees: 0, settlementRecorded: 0, settlementTrades: 2, net: null, feesTrades: 1, netRecorded: -0.96, basis: "" },
    });
    const html = render(
      payload({
        loops: [loop("flies"), meic],
        suite: { loops: 2, armed: [], net: null, gross: 2, fees: null, settlementFees: 0, trades: 2, summed: ["flies", "meic"], excluded: [], feesUnrecorded: ["meic"] },
      }),
    );
    expect(html).toContain("n/r (1 of 2)");
    expect(html).toContain("its net is unknown; -$0.96 over the other 1");
    expect(html).toContain("fees were never recorded on a trade resolved today in meic");
  });

  it("labels MEIC's breaker figure gross, and a net breaker without a label", () => {
    const html = render(
      payload({
        loops: [
          loop("flies", { breaker: { limit: 500, figure: -40, figureKind: "net", tripped: false, basis: "" } }),
          loop("meic", { breaker: { limit: 300, figure: 4, figureKind: "gross", tripped: false, basis: "" } }),
        ],
      }),
    );
    expect(html).toContain("ok · $4.00 gross of −$300.00");
    expect(html).toContain("ok · -$40.00 of −$500.00");
  });

  it("names MEIC's missing supervisor job rather than a missing loop record", () => {
    const html = render(payload({ loops: [loop("meic"), loop("bwb")] }));
    expect(html).toContain("no meic-live job");
    expect(html).toContain("no loop record");
  });
});

describe("the suite badge", () => {
  it("counts the loops armed now and names the halt flag", () => {
    expect(suiteBadgeText(payload())).toBe("none armed");
    expect(suiteBadgeText(payload({ halted: true, suite: { ...payload().suite, armed: ["flies", "meic"] } }))).toBe("2 armed · HALTED");
  });
});

/** Live → today in the simple and detail views (2026-10-21). */
describe("the loops table, simple and detail", () => {
  const shape = (view: "simple" | "detail", d: LiveTodayPayload) => tables(inView(view, () => render(d)))[0]!;

  it("names the loop's state `state`, and keeps the gate, the counts and the net's parts to detail", () => {
    const simple = shape("simple", payload());
    const detail = shape("detail", payload());
    expect(simple.headers.filter((h) => h === "loop")).toHaveLength(1);
    expect(detail.headers.filter((h) => h === "loop")).toHaveLength(1);
    expect(simple.headers).toContain("state");
    for (const h of ["gate · symbol", "entries", "resolved", "gross", "fees", "settlement", "working"]) {
      expect(simple.headers).not.toContain(h);
      expect(detail.headers).toContain(h);
    }
    expect(simple.headers).toEqual(expect.arrayContaining(["arm", "open", "worst case", "mark", "net", "loss breaker"]));
    expect(aligned(simple)).toBe(true);
    expect(aligned(detail)).toBe(true);
  });

  it("shows `working` in simple too whenever a loop has an order working", () => {
    const d = payload({ loops: [loop("flies"), loop("meic", { working: 1 }), loop("bwb")] });
    expect(shape("simple", d).headers).toContain("working");
    expect(aligned(shape("simple", d))).toBe(true);
  });

  it("a retired loop's folded line spans the simple view's columns", () => {
    const d = payload({ loops: [loop("flies", { moduleEnabled: false, retired: true }), loop("meic")] });
    expect(aligned(shape("simple", d))).toBe(true);
    expect(aligned(shape("detail", d))).toBe(true);
  });

  it("the hand row lines up with the loops in both views, with and without a working column", () => {
    for (const d of [payload({ hand: hand(HAND_IC) }), payload({ hand: hand(HAND_IC), loops: [loop("meic", { working: 1 })] })]) {
      expect(aligned(shape("simple", d))).toBe(true);
      expect(aligned(shape("detail", d))).toBe(true);
    }
  });
});

/** The hand row and card (2026-11-03): 0DTE trades placed by hand, which no loop manages. */
describe("the hand row", () => {
  it("states the hand net beside the loops' total and never adds it in", () => {
    const html = render(payload({ hand: hand(HAND_IC) }));
    expect(html).toContain("by hand");
    expect(html).toContain("By hand: $127.12 at mark, not in the loops&#x27; total.");
    // the loops' total is still the loops' alone
    expect(html).toContain("$0.00");
    expect(html).not.toContain("$127.12</span></div>");
  });

  it("lists each hand order with its legs, net price, cash and fees", () => {
    const html = render(payload({ hand: hand(HAND_IC) }));
    expect(html).toContain("By hand — SPX 0DTE, 2026-10-15");
    expect(html).toContain("−1 7555P  +1 7545P  −1 7590C  +1 7600C");
    expect(html).toContain("1.80 cr");
    expect(html).toContain("+$180.00");
    expect(html).toContain("$2.88");
    expect(html).toContain("11:35"); // 15:35Z in ET
  });

  it("names a leg the fills cannot explain, and draws no net for it", () => {
    const html = render(
      payload({
        hand: hand({
          ...HAND_IC,
          unreconciled: [{ symbol: "SPXW  261015C07605000", held: -1, loopFills: 0, handFills: 0 }],
          mark: null,
          gross: null,
          net: null,
        }),
      }),
    );
    expect(html).toContain("does not reconcile");
    expect(html).toContain("SPXW  261015C07605000: -1 held, but today&#x27;s fills explain 0");
    expect(html).toContain("By hand: n/r");
  });

  it("says why there is nothing to show before today's evaluation, and draws no card", () => {
    const html = render(payload());
    expect(html).toContain("no position evaluation today yet (newest 2026-10-14)");
    expect(html).toContain("No 0DTE trade by hand today.");
    expect(html).not.toContain("By hand —");
  });
});
