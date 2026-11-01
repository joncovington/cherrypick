import { describe, it, expect } from "vitest";
import { healthSentence } from "../src/pages/Backtest/RunCards";

/**
 * The run cards' health checks (2026-11-01): each check in a sentence against its bar. The flags and
 * the numbers are `cards.health`'s; the page only words them. Synthetic figures only.
 */

describe("the health check sentences", () => {
  it("says a result rests on its best sessions, or that it does not", () => {
    expect(healthSentence({ check: "best_sessions", flag: true, bar: "", k: 5, share: 2.4, rest_mean: -10 })).toBe(
      "Rests on its best 5 sessions: without them the rest average -$10.00 a session.",
    );
    expect(healthSentence({ check: "best_sessions", flag: false, bar: "", k: 5, share: 0.15, rest_mean: 10.12 })).toContain(
      "Not carried by a few days",
    );
  });

  it("names the bar only when a check is flagged", () => {
    expect(healthSentence({ check: "win_rate", flag: true, bar: 0.25, value: 0.025 })).toBe(
      "Only 2.5% of sessions win (under 25%): long losing runs are normal for it.",
    );
    expect(healthSentence({ check: "losing_run", flag: true, bar: 10, value: 39 })).toBe("Longest losing run: 39 sessions (10 or more).");
    expect(healthSentence({ check: "losing_run", flag: false, bar: 10, value: 4 })).toBe("Longest losing run: 4 sessions.");
  });

  it("says an open drawdown is not recovered, and words the edge and the fees", () => {
    expect(healthSentence({ check: "drawdown_length", flag: true, bar: 60, value: 80, recovered: false })).toBe(
      "Longest drawdown: 80 sessions, not recovered by the run's end (60 or more).",
    );
    expect(healthSentence({ check: "edge_trend", flag: true, bar: "", value: "decaying" })).toContain("decaying");
    expect(healthSentence({ check: "edge_trend", flag: false, bar: "", value: "within noise" })).toBe("Edge trend: within noise.");
    expect(healthSentence({ check: "fee_drag", flag: true, bar: 0.25, value: 0.31 })).toBe(
      "Fees take 31.0% of the premium collected (25% or more).",
    );
  });
});
