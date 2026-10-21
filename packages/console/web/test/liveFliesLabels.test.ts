import { describe, expect, it } from "vitest";
import type { LiveFliesPayload, LivePeriod } from "@console/shared";
import { markTileLabel, periodTitle } from "../src/pages/Live/LivePage";
import { fliesBadgeText } from "../src/lightbox/manifests/LiveLightbox";

/**
 * The Live → flies tab's labels after the module was retired (2026-10-21): a five-session-old mark
 * said "now", the header said "not armed" and the loop "idle" for a switched-off module, and the
 * period tiles called a year of paper beside three weeks of live "the same period".
 */

const arm = (over: Partial<LiveFliesPayload["arm"]> = {}): LiveFliesPayload["arm"] => ({
  armed: false,
  date: null,
  stale: false,
  halted: false,
  liveEnabled: false,
  arm: "control",
  symbol: "SPX",
  ...over,
});

describe("the mark tile", () => {
  it("says now only for today's running session, and names any other", () => {
    expect(markTileLabel({ session: "2026-10-21", sessionBasis: "today" })).toBe("now · mark (mid)");
    expect(markTileLabel({ session: "2026-10-16", sessionBasis: "last_completed" })).toBe("2026-10-16 · last mark (mid)");
    expect(markTileLabel({ session: "2026-10-14", sessionBasis: "requested" })).toBe("2026-10-14 · last mark (mid)");
  });
});

describe("the header badge", () => {
  it("says module off for a switched-off module, never 'not armed'", () => {
    expect(fliesBadgeText({ arm: arm(), moduleEnabled: false, session: "2026-10-16" })).toBe("flies · module off · history to 2026-10-16");
    expect(fliesBadgeText({ arm: arm(), moduleEnabled: true, session: "2026-10-16" })).toBe("not armed · control · SPX");
    expect(fliesBadgeText({ arm: arm(), moduleEnabled: null, session: "2026-10-16" })).toBe("not armed · control · SPX");
  });

  it("never hides a halt or a live arm behind module off", () => {
    expect(fliesBadgeText({ arm: arm({ halted: true }), moduleEnabled: false, session: "2026-10-16" })).toMatch(/^HALTED/);
    expect(fliesBadgeText({ arm: arm({ armed: true }), moduleEnabled: false, session: "2026-10-16" })).toMatch(/^ARMED/);
  });
});

describe("the period tile's comparison", () => {
  const p: LivePeriod = {
    net: -857.36,
    trades: 112,
    sessions: 21,
    paperNet: 410.5,
    paperSessions: 20,
    onRisk: { net: 0, peakRisk: 0, ratio: null, sessions: 0, of: 0, replayed: 0, booked: 0 },
  };

  it("names the arm and both session counts, and never says 'the same period'", () => {
    const t = periodTitle(p, "control");
    expect(t).toContain("the control arm only: 21 sessions, 112 structures");
    expect(t).toMatch(/paper control over the same 21 sessions live traded: \+?\$410\.50 \(it settled on 20 of them\)/);
    expect(t).not.toContain("same period");
  });
});
