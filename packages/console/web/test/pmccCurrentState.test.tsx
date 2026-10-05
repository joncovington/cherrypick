import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { PmccArmCell, PmccOpenPosition, PmccPayload } from "@console/shared";
import { BookComparison, OpenTradesCard } from "../src/pages/Pmcc/CurrentStateCards";

const text = (node: React.ReactElement) =>
  renderToString(<MemoryRouter>{node}</MemoryRouter>).replace(/<!--\s*-->/g, "");

function position(over: Partial<PmccOpenPosition>): PmccOpenPosition {
  return {
    positionId: "XSP:shield:2026-10-05",
    symbol: "XSP",
    arm: "shield",
    status: "open",
    longStrike: 550,
    longExpiration: "2027-09-17",
    shortStrike: 765,
    shortExpiration: "2026-10-16",
    entrySpot: 773.02,
    netDebit: 236.21,
    quantity: 1,
    entryCash: -23621,
    entryNetTv: null,
    entryWeeklyYieldPct: 0.011,
    downsideProtectionPct: 0.29,
    breakeven: null,
    rollCount: 0,
    lifecycle: "held_long",
    currentShortTv: 3.405,
    currentSpot: 775.67,
    lastMarkAt: null,
    exposedTicks: 0,
    markedTicks: 0,
    entryMaxSpreadPct: null,
    entryMaxSpreadAbs: null,
    entrySession: "2026-10-05",
    unrealisedGross: 46,
    unrealisedNet: 22.38,
    feesToDate: 23.62,
    ...over,
  };
}

function payload(open: PmccOpenPosition[], arms: PmccArmCell[] = []): PmccPayload {
  return {
    session: "2026-10-05",
    dbPresent: true,
    openPositions: open,
    openCount: open.length,
    arms,
    params: {
      tvCloseThreshold: 0.05,
      tvManagedExit: false,
      assignmentExposureTv: 0.05,
      longDeltaMin: 0.85,
      longDeltaMax: 0.9,
      symbols: ["XSP", "QQQ"],
      settlementStyle: { XSP: "cash", QQQ: "physical" },
    },
  } as unknown as PmccPayload;
}

const control = position({
  positionId: "QQQ:control:2026-10-05",
  symbol: "QQQ",
  arm: "control",
  longStrike: 700,
  longExpiration: "2026-10-23",
  shortStrike: 751,
  lifecycle: "weekly",
  currentShortTv: 8.34,
});

describe("PMCC open trades", () => {
  it("splits held-long from weekly rows and states every expiry with its year", () => {
    const html = text(<OpenTradesCard data={payload([position({}), control])} />);
    expect(html).toContain("held long");
    expect(html).toContain("weekly — control, running off");
    // A year-long long and a weekly short no longer read alike ("09-17" vs "10-16").
    expect(html).toContain("550 · 2027-09-17 ·");
    expect(html).toContain("765 · 2026-10-16");
    expect(html).toContain("2026-10-05"); // opened, with its year
  });

  it("draws control's time-value exit threshold on weekly rows only", () => {
    const held = text(<OpenTradesCard data={payload([position({})])} />);
    expect(held).not.toContain("→ $0.05");
    expect(held).not.toContain("weekly yield");
    const weekly = text(<OpenTradesCard data={payload([control])} />);
    expect(weekly).toContain("→ $0.05");
  });
});

describe("PMCC arm comparison", () => {
  it("files an advisor arm by its prefix, never shield as one", () => {
    const cell = (arm: string, net: number): PmccArmCell => ({
      arm,
      symbol: "XSP",
      positions: 1,
      grossPnl: net,
      fees: 0,
      netPnl: net,
      winRate: 1,
      rolls: 0,
    });
    const html = text(
      <BookComparison data={payload([], [cell("shield", 120), cell("advised:tv-exit-threshold-floor", 30)])} />,
    );
    const advised = html.slice(html.indexOf("advised arms"), html.indexOf("net by arm"));
    expect(advised).toContain("advised:tv-exit-threshold-floor");
    expect(advised).not.toContain(">shield<");
  });
});
