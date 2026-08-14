import { describe, it, expect, afterEach } from "vitest";
import { readScreenMetrics, resetScreenCache, setScreenCaller } from "../src/services/screenBridge.js";

/**
 * The console asks the earnings module for classified screening metrics rather than deriving its
 * own. It derived its own once and disagreed with `screen_report` about which gate to move — the
 * only question the card exists to answer — because a raw `scan_log` count pools four retired reason
 * vocabularies and cannot tell a gate doing its job from one standing behind five others.
 */

afterEach(() => {
  setScreenCaller();
  resetScreenCache();
});

const metrics = {
  profile: "strat_test",
  since: "2026-08-12",
  funnel: { screened_decisions: 114, screened_symbols: 19, accepted: 0, rejected: 114, opened: 0 },
  reasons: [{ reason: "avg_volume_below_minimum", total: 27, sole: 24, strategies: 6 }],
  sole: [],
  excluded: [{ label: "retired tier ladder", rows: 1550 }],
  coverage: {},
};

describe("the screening metrics bridge", () => {
  it("passes the classified metrics through untouched", () => {
    setScreenCaller(() => ({ ok: true, metrics: metrics as never, error: null }));
    const out = readScreenMetrics("paper", "2026-08-12");
    expect(out.ok).toBe(true);
    expect(out.metrics?.reasons[0]).toMatchObject({ reason: "avg_volume_below_minimum", total: 27, sole: 24 });
  });

  it("memoises, because classifying the whole scan history is a subprocess and the page polls", () => {
    let calls = 0;
    setScreenCaller(() => {
      calls += 1;
      return { ok: true, metrics: metrics as never, error: null };
    });
    readScreenMetrics("paper", null, 1_000);
    readScreenMetrics("paper", null, 30_000);
    expect(calls).toBe(1);
    // Past the TTL it asks again — a scan since then would change the answer.
    readScreenMetrics("paper", null, 1_000 + 200_000);
    expect(calls).toBe(2);
  });

  it("keys the cache by mode and window, so paper and live never answer for each other", () => {
    let calls = 0;
    setScreenCaller(() => {
      calls += 1;
      return { ok: true, metrics: metrics as never, error: null };
    });
    readScreenMetrics("paper", null, 1_000);
    readScreenMetrics("live", null, 1_000);
    readScreenMetrics("paper", "2026-08-12", 1_000);
    expect(calls).toBe(3);
  });

  it("reports an unavailable module as an error rather than an empty histogram", () => {
    // An empty card reads as "nothing was rejected", which is the opposite of the truth.
    setScreenCaller(() => ({ ok: false, metrics: null, error: "screening metrics unavailable — ..." }));
    const out = readScreenMetrics("paper", null);
    expect(out.ok).toBe(false);
    expect(out.metrics).toBeNull();
    expect(out.error).toContain("unavailable");
  });
});
