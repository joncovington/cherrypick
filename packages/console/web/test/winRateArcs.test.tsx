import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { NetHero, WinRateArcs, intervalLine, overlapNote, rLine, skewNote, type TradeShape } from "../src/components/summary/kit";

/**
 * A win rate is never shown alone (B2; Goodhart: a premium seller can win 85% of its trades and lose
 * money). The card always carries net per trade beside the rate, takes its tone from net per trade
 * rather than from the rate passing 50%, and gives the rate its interval.
 */

const losingSeller: TradeShape = {
  median: 10,
  avgWin: 10,
  avgLoss: -100,
  worst: -100,
  n: 20,
  mean: -6.5,
  sd: 40,
  ci95: [-25.2, 12.2],
  t: -0.73,
  winRateCi95: [0.64, 0.95],
  skewness: -3.1,
  skew: "left",
};

function render(el: JSX.Element): string {
  return renderToString(el).replace(/<!-- -->/g, "");
}

describe("the win-rate card", () => {
  it("shows net per trade beside the rate, always", () => {
    const html = render(<WinRateArcs daysWon={8} days={10} wins={17} trades={20} shape={losingSeller} />);
    expect(html).toContain("net per trade");
    expect(html).toContain("-$6.50");
    expect(html).toContain("95% -$25.20 to +$12.20");
  });

  it("colours an 85% win rate by its losing net per trade, never green for passing 50%", () => {
    const html = render(<WinRateArcs daysWon={8} days={10} wins={17} trades={20} shape={losingSeller} />);
    expect(html).toMatch(/class="ts-neg"[^>]*>85%/);
    expect(html).not.toMatch(/class="ts-pos"[^>]*>85%/);
  });

  it("gives the rate its interval, and carries no tone with no net per trade", () => {
    const html = render(<WinRateArcs daysWon={8} days={10} wins={17} trades={20} shape={losingSeller} />);
    expect(html).toContain("(95% 64–95%)");
    const bare = render(<WinRateArcs daysWon={1} days={2} wins={3} trades={4} />);
    expect(bare).toMatch(/class="ts-plain"[^>]*>75%/);
    expect(bare).toContain("net per trade n/r");
  });
});

describe("the net card's interval line and skew note", () => {
  it("states the interval and t, or nothing for a shape without one", () => {
    expect(intervalLine(losingSeller)).toBe("95% -$25.20 to +$12.20 · t -0.7");
    expect(intervalLine({ median: 1, avgWin: 1, avgLoss: null, worst: 1 })).toBeNull();
  });

  it("names premium selling's left skew for what it is", () => {
    expect(skewNote(losingSeller)).toContain("rare large losses");
    expect(skewNote({ ...losingSeller, skew: "right" })).toContain("large wins");
    expect(skewNote({ ...losingSeller, skew: null })).toBeNull();
  });

  it("renders the total face by default without the interval", () => {
    const html = render(<NetHero net={-130} trades={20} shape={losingSeller} />);
    expect(html).toContain("total over 20 trades");
    expect(html).not.toContain("95%");
  });
});

describe("results in R (B3)", () => {
  it("states mean R with its interval and how many trades carried a risk", () => {
    const shape = { ...losingSeller, meanR: -0.065, rN: 18, rCi95: [-0.25, 0.12] as [number, number] };
    expect(rLine(shape, 20)).toBe("-0.07R per trade (95% -0.25R to 0.12R) over 18 of 20");
    expect(rLine({ ...shape, rN: 20 }, 20)).toBe("-0.07R per trade (95% -0.25R to 0.12R)");
    expect(rLine(losingSeller, 20)).toBeNull();
  });
});

describe("the overlap note (B2)", () => {
  it("says the interval reads narrow when sessions closed several trades", () => {
    expect(overlapNote({ ...losingSeller, overlap: { sessions: 4, of: 10, maxPerSession: 6 } })).toBe(
      "4 of 10 sessions closed several trades (up to 6): the interval treats them as independent, so it reads narrower than it is",
    );
    expect(overlapNote(losingSeller)).toBeNull();
  });
});

describe("the multi-day overlap note", () => {
  it("names trades held together", () => {
    expect(overlapNote({ ...losingSeller, overlap: { sessions: 0, of: 5, maxPerSession: 1, heldTogether: 4 } })).toBe(
      "4 trades were held alongside another in their arm: the interval treats them as independent, so it reads narrower than it is",
    );
  });
});

