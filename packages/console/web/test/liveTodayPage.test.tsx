import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { LiveMeicPayload, LiveTodayHand, LiveTodayLoop, LiveTodayPayload } from "@console/shared";
import { LiveTodayView, LoopDetail, openAtMark, suiteBadgeText } from "../src/pages/Live/LiveTodayPage";
import { DayChains, bwbChains, meicChains } from "../src/pages/Live/LiveTodayOrders";
import { expiredWords, handModel } from "../src/pages/Live/HandToday";
import { inView } from "./viewTables";

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
    expiredHeld: [],
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

/** A made-up hand iron condor still held: +$220 in, $2.00 fees, the four legs at a −$50 mark. */
const HAND_IC: Partial<LiveTodayHand> = {
  orders: [
    {
      orderId: "200",
      at: "2026-10-15T15:35:00Z",
      kind: "open",
      legs: [
        { symbol: "SPXW  261015P04980000", action: "Sell to Open", quantity: 1, price: 2.5, value: 250 },
        { symbol: "SPXW  261015P04970000", action: "Buy to Open", quantity: 1, price: 1.2, value: -120 },
        { symbol: "SPXW  261015C05080000", action: "Sell to Open", quantity: 1, price: 1.3, value: 130 },
        { symbol: "SPXW  261015C05090000", action: "Buy to Open", quantity: 1, price: 0.4, value: -40 },
      ],
      cash: 220,
      fees: 2,
    },
  ],
  openLegs: [
    { symbol: "SPXW  261015P04980000", right: "P", strike: 4980, quantity: -1, mid: 0.5, value: -50, markSource: "feed", markAgeSeconds: 1 },
  ],
  loopFills: 6,
  cash: 220,
  mark: -50,
  gross: 170,
  fees: 2,
  settlement: null,
  net: 168,
  netBasis: "at mark: held legs at mid, not yet settled",
};

const render = (d: LiveTodayPayload) =>
  renderToString(
    <MemoryRouter>
      <LiveTodayView data={d} />
    </MemoryRouter>,
  );

const detailOf = (l: LiveTodayLoop) =>
  renderToString(
    <MemoryRouter>
      <LoopDetail l={l} attention={[]} />
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
    // settlement and fees are the detail tab's fields (2026-11-04)
    expect(detailOf(flies)).toContain("n/r (1 of 2)");
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
    expect(detailOf(meic)).toContain("n/r (1 of 2)");
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

/** Live → today as cards (2026-11-04): each loop's card carries the simple view's fields; its
 *  lightbox's detail tab carries every field the old table's detail view did. */
describe("the loop cards and their detail", () => {
  const labels = (html: string) => [...html.matchAll(/<th scope="row">([^<]*)<\/th>/g)].map((m) => m[1]!.replace(/&amp;/g, "&"));

  const tileLabels = (html: string) => [...html.matchAll(/<span class="stat-label">([^<]*)<\/span>/g)].map((m) => m[1]!);

  it("a card shows the open count, worst case, mark and breaker; the gate, counts and the net's parts wait for the detail", () => {
    const html = render(payload({ loops: [loop("meic")], hand: undefined }));
    const card = tileLabels(html);
    expect(card).toEqual(["open", "worst case", "mark"]);
    expect(html).toContain("loss breaker");
    expect(html).toContain("no meic-live job");
    for (const h of ["gate · symbol", "entries", "resolved", "settlement", "working"]) expect(html).not.toContain(`>${h}<`);
    const detail = labels(renderToString(<MemoryRouter><LoopDetail l={loop("meic")} attention={[]} /></MemoryRouter>));
    for (const h of ["gate · symbol", "entries", "resolved", "gross", "fees", "settlement", "working", "net", "arm"]) expect(detail).toContain(h);
  });

  it("shows `working` on the cards whenever a loop has an order working", () => {
    const d = payload({ loops: [loop("flies"), loop("meic", { working: 1 }), loop("bwb")], hand: undefined });
    expect(tileLabels(render(d))).toContain("working");
  });

  it("the big card links to Live → positions, and a loop card to its own page", () => {
    const html = render(payload());
    expect(html).toContain('href="/live/positions"');
    expect(html).toContain('href="/live/meic"');
  });

  it("marks a losing loop's card red, and names what needs a person in its detail", () => {
    const meic = loop("meic", { today: { trades: 1, gross: -50, fees: 2, settlementFees: 0, settlementRecorded: 0, settlementTrades: 1, net: -52, feesTrades: 1, netRecorded: -52, basis: "" } });
    expect(render(payload({ loops: [meic] }))).toContain("lt-loop is-neg");
    const detail = renderToString(
      <MemoryRouter>
        <LoopDetail l={meic} attention={[{ loop: "meic", kind: "orphaned_orders", detail: "1 working broker order no live ledger knows", to: "/live/meic" }]} />
      </MemoryRouter>,
    );
    expect(detail.replace(/<!-- -->/g, "")).toContain("orphaned orders: 1 working broker order no live ledger knows");
  });
});

/** A loop's orders today as chains, from its own live read. */
describe("the loop order chains", () => {
  const meicOpen = {
    id: 7, tradeDate: "2026-10-15", entryTime: "2026-10-15T10:05:00", symbol: "SPX", arm: "live", status: "open", working: false,
    putStrike: 5800, callStrike: 5900, wingWidth: 25, quantity: 1, netCredit: 2.6, entryCash: 260,
    putHeld: false, callHeld: true, putClosing: false, callClosing: false, fillConfirmedAt: null, overdue: false, mark: null,
  };
  const meicDone = {
    mode: "live" as const, id: 6, tradeDate: "2026-10-15", entryTime: "2026-10-15T09:45:00", symbol: "SPX", putStrike: 5795, callStrike: 5905,
    wingWidth: 25, netCredit: 2.4, quantity: 1, status: "expired", entryCash: 240, exitCash: 0, exitKind: "expired" as const, gross: 240,
    fees: 4, settlementFees: 0, slippage: null, net: 236, exitReason: "expired worthless", ivRankAtEntry: null,
  };

  it("MEIC: an open condor with a stopped side, then today's resolved ones; yesterday's history is left out", () => {
    const chains = meicChains({ session: "2026-10-15", open: [meicOpen], history: [meicDone, { ...meicDone, id: 5, tradeDate: "2026-10-14" }] } as unknown as LiveMeicPayload);
    expect(chains.map((c) => c.id)).toEqual(["meic-7", "meic-6"]);
    expect(chains[0]!.steps.map((x) => x.role)).toEqual(["open", "close"]);
    expect(chains[0]!.steps[0]!.what).toBe("5775/5800P · 5900/5925C");
    expect(chains[0]!.steps[0]!.price).toBe("2.60 cr");
    expect(chains[1]!.net).toBe(236);
    expect(chains[1]!.steps.map((x) => x.role)).toEqual(["open", "expired"]);
  });

  it("bwb: the add-on joins the chain once it fired, and only today's closes are listed", () => {
    const open = { positionId: "p1", symbol: "SPX", arm: "control", status: "open", bodyStrike: 5800, nearStrike: 5830, farStrike: 5780, expiration: "2026-10-31", entrySpot: null, entryCredit: 1.1, entryMaxLoss: null, quantity: 1, entryCash: 110, addOnCash: 45, peakAbsDelta: null, belowFlipSeen: false, armedAt: null, addonFiredAt: "2026-10-15T11:20:00", addonShortStrike: 5760, addonLongStrike: 5750, addonCredit: 0.45, currentCloseCost: null, currentSpot: null, entrySession: "2026-10-13", unrealisedGross: null, unrealisedNet: -30, feesToDate: 3 };
    const closed = { positionId: "p0", symbol: "SPX", arm: "control", entrySession: "2026-10-10", closedSession: "2026-10-15", status: "closed", exitReason: "target", bodyStrike: 5700, nearStrike: 5730, farStrike: 5680, expiration: "2026-10-24", entrySpot: null, entryCredit: 1.0, armedAt: null, addonFiredAt: null, addonShortStrike: null, addonLongStrike: null, addonCredit: null, quantity: 1, entryCash: 100, addOnCash: null, exitCash: 120, exitKind: "closed" as const, grossPnl: 220, fees: 4, settlementFees: 0, slippage: 1, netPnl: 215 };
    const chains = bwbChains([open] as never, [closed, { ...closed, positionId: "old", closedSession: "2026-10-14" }] as never, "2026-10-15");
    expect(chains.map((c) => c.id)).toEqual(["bwb-p1", "bwb-p0"]);
    expect(chains[0]!.steps.map((x) => x.role)).toEqual(["open", "add"]);
    expect(chains[0]!.net).toBe(-30);
    expect(chains[1]!.steps.map((x) => x.role)).toEqual(["open", "closed"]);
    expect(chains[1]!.net).toBe(215);
  });

  it("draws a resolved chain's net, red when it lost", () => {
    const html = renderToString(<DayChains chains={[{ id: "x", title: "SPX iron condor", sub: "", steps: [], open: false, net: -40, netLabel: "net" }]} empty="none" />);
    expect(html).toContain("lt-chain is-neg");
    expect(html).toContain("ended · net");
  });
});

/** The hand row and card (2026-11-03): 0DTE trades placed by hand, which no loop manages. */
describe("the hand row", () => {
  it("states the hand net beside the loops' total and never adds it in", () => {
    const html = render(payload({ hand: hand(HAND_IC) }));
    expect(html).toContain("by hand");
    expect(html).toContain("By hand: $168.00 at mark, not in the loops&#x27; total.");
    // the loops' total is still the loops' alone
    expect(html).toMatch(/net today · all loops[\s\S]*?class="pf-hero pnl-pos"[^>]*>\$0\.00</);
  });

  it("lists each hand order with its legs, net price, cash and fees", () => {
    const html = render(payload({ hand: hand(HAND_IC) }));
    expect(html).toContain("by hand · SPX 0DTE");
    // one trade card, its legs in the order-chain look
    expect(html.match(/tc-card/g)?.length).toBe(1);
    for (const leg of ["1 4980P @ 2.50", "1 4970P @ 1.20", "1 5080C @ 1.30", "1 5090C @ 0.40"]) expect(html).toContain(leg);
    expect(html.match(/>STO</g)?.length).toBe(2);
    expect(html.match(/>BTO</g)?.length).toBe(2);
    expect(html).toContain("2.20 cr");
    expect(html).toContain("+$220.00");
    expect(html).toContain("fees $2.00");
    expect(html).toContain("11:35"); // 15:35Z in ET
    expect(html).toContain("no loop manages these"); // the amber flag stays on the card
  });

  it("names a leg the fills cannot explain, and draws no net for it", () => {
    const html = render(
      payload({
        hand: hand({
          ...HAND_IC,
          unreconciled: [{ symbol: "SPXW  261015C05100000", held: -1, loopFills: 0, handFills: 0 }],
          mark: null,
          gross: null,
          net: null,
        }),
      }),
    );
    expect(html).toContain("does not reconcile");
    expect(html).toContain("SPXW  261015C05100000: -1 held, but today&#x27;s fills explain 0");
    expect(html).toContain("By hand: n/r");
    expect(html).toMatch(/class="lv-state is-bad"[^>]*>SPXW  261015C05100000: -1 held/);
  });

  it("lists yesterday's expired legs muted, never red, on a morning with no hand trade", () => {
    const html = render(
      payload({
        hand: hand({
          expiredHeld: [
            { symbol: "SPXW  261014P04950000", expiry: "2026-10-14", quantity: -1 },
            { symbol: "SPXW  261014P04940000", expiry: "2026-10-14", quantity: 1 },
          ],
          caveats: ["the account history holds nothing older than today, so today's fills may not all be in it"],
        }),
      }),
    );
    expect(html).toContain("by hand ·");
    expect(html).toMatch(/class="lv-state"[^>]*>2 expired legs still listed by the broker until it processes the expiry, not counted here \(2026-10-14: −1 4950P \+1 4940P\)/);
    expect(html).toMatch(/class="lv-state"[^>]*>the account history holds nothing older than today/);
    expect(html).not.toContain("is-bad");
  });

  it("names one expired leg in the singular", () => {
    expect(expiredWords([{ symbol: "SPXW  261014C05100000", expiry: "2026-10-14", quantity: -1 }])).toBe(
      "1 expired leg still listed by the broker until it processes the expiry, not counted here (2026-10-14: −1 5100C)",
    );
    expect(expiredWords([])).toBeNull();
  });

  it("says why there is nothing to show before today's evaluation, and draws no card", () => {
    const html = render(payload());
    expect(html).toContain("no position evaluation today yet (newest 2026-10-14)");
    expect(html).toContain("No 0DTE trade by hand today.");
    expect(html).not.toContain("tc-card");
  });

  it("lists what is open at each loop's own mark under the realised net, never summed, n/r where a loop cannot mark", () => {
    const d = payload({
      loops: [
        loop("flies", { open: 0, mark: null }),
        loop("meic", { open: 2, mark: { value: 50, at: "2030-01-02T17:44:10.000Z", basis: "before fees" } }),
        loop("bwb", { open: 1, mark: { value: null, at: null, basis: "n/r when any is unmarked" } }),
      ],
      hand: hand(HAND_IC),
    });
    const items = openAtMark(d);
    expect(items.map((i) => [i.label, i.value])).toEqual([["meic", 50], ["bwb", null], ["by hand", 168]]);
    expect(items[0]!.title).toContain("before fees");
    const html = render(d).replace(/<!-- -->/g, "");
    expect(html).toContain('aria-label="open at mark"');
    expect(html).toMatch(/meic <span class="pnl-pos">\$50\.00/);
    expect(html).toMatch(/bwb <span class="muted">n\/r/);
    // listed, never added into one figure or into the loops' total
    expect(html).not.toContain("$218.00");
    expect(html).toMatch(/net today · all loops[\s\S]*?class="pf-hero pnl-pos"[^>]*>\$0\.00</);
  });

  it("says nothing is open when no loop holds anything and no hand leg is held", () => {
    const d = payload({ loops: [loop("flies", { open: 0, mark: null }), loop("meic", { open: 0, mark: { value: null, at: null, basis: "x" } })] });
    expect(openAtMark(d)).toEqual([]);
    expect(render(d)).toContain(">nothing open<");
  });
});
