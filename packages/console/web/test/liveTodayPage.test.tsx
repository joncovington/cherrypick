import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { LiveTodayLoop, LiveTodayPayload } from "@console/shared";
import { LiveTodayView, suiteBadgeText } from "../src/pages/Live/LiveTodayPage";

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
    ...over,
  };
}

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
