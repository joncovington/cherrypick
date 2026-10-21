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
    today: { trades: 0, gross: 0, fees: 0, settlementFees: 0, settlementRecorded: 0, settlementTrades: 0, net: 0, basis: "" },
    mark: null,
    breaker: { limit: null, figure: null, tripped: null, basis: "" },
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
    suite: { loops: 3, armed: [], net: 0, gross: 0, fees: 0, settlementFees: 0, trades: 0, summed: ["flies", "meic", "bwb"], excluded: [] },
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
      today: { trades: 2, gross: 80, fees: 11, settlementFees: null, settlementRecorded: 5, settlementTrades: 1, net: 64, basis: "" },
    });
    const html = render(
      payload({
        loops: [flies, loop("meic"), loop("bwb", { ledger: "failed", ledgerError: "boom", today: null })],
        suite: { loops: 3, armed: [], net: null, gross: null, fees: null, settlementFees: null, trades: null, summed: [], excluded: [{ loop: "bwb", reason: "the live ledger could not be read" }] },
      }),
    );
    expect(html).toContain("n/r (1 of 2)");
    expect(html).toContain("read failed");
    expect(html).toMatch(/net today · all loops[\s\S]*?n\/r/);
  });
});

describe("the suite badge", () => {
  it("counts the loops armed now and names the halt flag", () => {
    expect(suiteBadgeText(payload())).toBe("none armed");
    expect(suiteBadgeText(payload({ halted: true, suite: { ...payload().suite, armed: ["flies", "meic"] } }))).toBe("2 armed · HALTED");
  });
});
