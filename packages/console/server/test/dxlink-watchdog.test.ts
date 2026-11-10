/**
 * The DXLink silent-death watchdog. The SDK leaves a dropped session reading "connected", so the
 * console rebuilds it after a minute of silence, but only when something should have ticked: SPX
 * in regular hours, or, at any hour, a symbol a viewer holds that the Python streamer quoted in
 * the last 30 s on its own session. The second rule is the overnight case (futures chips frozen on
 * a cached price after 16:00 while the session stayed "connected").
 */
import { describe, it, expect } from "vitest";
import { feedLooksDead } from "../src/market/marketData.js";

const MON_2330 = { etWeekday: "Mon", etMinutes: 23 * 60 + 30 };
const MON_1100 = { etWeekday: "Mon", etMinutes: 11 * 60 };
const SAT_1100 = { etWeekday: "Sat", etMinutes: 11 * 60 };

describe("feedLooksDead", () => {
  it("never calls a session dead inside a minute of its last event", () => {
    expect(feedLooksDead({ silenceMs: 59_000, ...MON_1100, streamerHasFreshViewedSymbol: () => true })).toBe(false);
  });
  it("in regular hours, a minute of silence is dead on its own (the SPX sentinel)", () => {
    expect(feedLooksDead({ silenceMs: 61_000, ...MON_1100, streamerHasFreshViewedSymbol: () => false })).toBe(true);
  });
  it("overnight, silence while the streamer quotes a viewed symbol is dead", () => {
    expect(feedLooksDead({ silenceMs: 61_000, ...MON_2330, streamerHasFreshViewedSymbol: () => true })).toBe(true);
  });
  it("overnight with nothing ticking anywhere, silence is a quiet tape, not a dead session", () => {
    expect(feedLooksDead({ silenceMs: 600_000, ...MON_2330, streamerHasFreshViewedSymbol: () => false })).toBe(false);
    expect(feedLooksDead({ silenceMs: 600_000, ...SAT_1100, streamerHasFreshViewedSymbol: () => false })).toBe(false);
  });
  it("reads the stream cache only once the silence is already long", () => {
    let reads = 0;
    feedLooksDead({ silenceMs: 1_000, ...MON_2330, streamerHasFreshViewedSymbol: () => (reads += 1) > 0 });
    expect(reads).toBe(0);
  });
});
