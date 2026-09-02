/**
 * Ported from scout's test_narrative.py. Every sentence this module produces is generated from a
 * detected condition with the numbers inline, so these tests pin the *detection* and the exact
 * wording together — a narrative that quietly changes what it claims is the failure mode worth
 * guarding against.
 */
import { describe as suite, it, expect } from "vitest";
import type { Bar, Level } from "../src/analytics/levels.js";
import {
  priceAction,
  scanHeadline,
  cci,
  technicalBullet,
  optionsBullet,
  relativeStrengthBullet,
  eventWarnings,
} from "../src/analytics/narrative.js";

function bars(closes: number[], spread = 1.0, volumes?: number[], startT = 1_700_000_000): Bar[] {
  return closes.map((c, i) => ({
    t: startT + i * 86400,
    o: c,
    h: c + spread,
    l: c - spread,
    c,
    v: volumes ? volumes[i]! : 100,
  }));
}
const flat = (n: number, v = 100): number[] => Array.from({ length: n }, () => v);
const TODAY = "2027-01-05";

suite("price action", () => {
  it("prefers the 200-day cross over everything else", () => {
    const closes = [...flat(220), 99, 106]; // crosses both the 200- and 50-day SMAs today
    const text = priceAction("TEST", bars(closes), [], "bullish", null, TODAY);
    expect(text).toContain("200-day moving average");
    expect(text).toContain("crossed above");
  });

  it("reports a big three-session move when no cross fired", () => {
    const closes = [...flat(60), 100, 103, 106.5]; // +6.5% in 3 sessions
    expect(priceAction("TEST", bars(closes), [], null, null, TODAY)).toContain("6.50% higher");
  });

  it("falls back to trend plus levels when nothing at all happened", () => {
    const levels: Level[] = [
      { price: 90, kind: "support", touches: 2 },
      { price: 110, kind: "resistance", touches: 3 },
    ];
    // Perfectly flat: no crosses, gaps, breaks, moves or bounces.
    const text = priceAction("TEST", bars(flat(60), 0), levels, "neutral", null, TODAY);
    expect(text).toBe("TEST is in a neutral trend with support at 90.00 and resistance at 110.00.");
  });

  it("appends the earnings suffix when the report is tomorrow", () => {
    const earnings = { expected_report_date: "2027-01-06", time_of_day: "BTO" };
    const text = priceAction("TEST", bars(flat(60), 0), [], "bullish", earnings, TODAY);
    expect(text).toContain("reports earnings tomorrow before the open");
  });

  it("reports role reversal on a level break", () => {
    const closes = [...flat(30), 99, 104];
    const levels: Level[] = [{ price: 102, kind: "resistance", touches: 3 }];
    const text = priceAction("TEST", bars(closes, 0.5), levels, null, null, TODAY);
    expect(text).toContain("broke above");
    expect(text).toContain("now becomes support");
  });
});

suite("scan headline", () => {
  it("names a bullish trend-following setup", () => {
    const r = scanHeadline("TEST", "mildly_bearish", "bullish");
    expect(r).not.toBeNull();
    expect(r!.scan).toBe("Bullish Trend Following");
    expect(r!.text).toContain("pulled back");
  });

  it("names a bearish trend-following setup", () => {
    expect(scanHeadline("TEST", "mildly_bullish", "bearish")!.scan).toBe("Bearish Trend Following");
  });

  it("is absent when no setup matches, rather than inventing one", () => {
    expect(scanHeadline("TEST", "bullish", "bullish")).toBeNull();
    expect(scanHeadline("TEST", null, null)).toBeNull();
  });

  it("prefers the more specific CCI dip when one is present", () => {
    const closes = [...Array.from({ length: 40 }, (_, i) => 100 + i * 0.5), 112, 108, 104];
    const b = bars(closes, 0.5);
    const value = cci(b);
    expect(value).not.toBeNull();
    expect(value!).toBeLessThan(-100);
    expect(scanHeadline("TEST", "mildly_bearish", "bullish", b)!.scan).toBe("CCI Dip in Bullish Trend");
  });
});

suite("technical bullet", () => {
  it("fires a golden cross on exactly one day in the window", () => {
    const closes = [...flat(210), ...Array.from({ length: 79 }, (_, i) => 100 + i + 1)];
    const fired: number[] = [];
    for (let end = 201; end <= closes.length; end++) {
      const text = technicalBullet("TEST", bars(closes.slice(0, end)));
      if (text?.includes("golden cross")) fired.push(end);
    }
    expect(fired).toHaveLength(1);
  });

  it("calls a new 52-week high, and proximity to one", () => {
    // Deliberately under 201 bars: a longer flat-then-spike series fires a golden cross, which
    // outranks this by design, so the fixture has to stay short enough to isolate the 52-week rung.
    expect(technicalBullet("TEST", bars([...flat(100), 120]))).toContain("new 52-week closing high");
    const near = [...flat(100), 120, ...flat(50), 118];
    expect(technicalBullet("TEST", bars(near))).toContain("of its 52-week high");
  });

  it("counts a streak, and refuses a choppy series", () => {
    const streaky = [...flat(30), 101, 102, 103, 104, 105, 106];
    expect(technicalBullet("TEST", bars(streaky))).toContain("closed higher 6 sessions in a row");
    const choppy = [100, 101, 100.5, 101.5, 100.8, 102, 101];
    const text = technicalBullet("TEST", bars(choppy));
    expect(text === null || !text.includes("sessions in a row")).toBe(true);
  });
});

suite("options bullet", () => {
  it("prefers IV vs realized, then falls back to IV rank", () => {
    expect(optionsBullet("TEST", { iv_30d: 0.6, hv_30d: 0.4, iv_rank: "0.9" })).toContain(
      "1.5x realized",
    );
    expect(optionsBullet("TEST", { iv_30d: 0.6, iv_rank: "0.9" })).toContain("IV rank is 90/100");
    expect(optionsBullet("TEST", {})).toBeNull();
  });
});

suite("relative strength", () => {
  it("needs a real gap before it claims anything", () => {
    const sym = [...flat(64), 130]; // +30%
    const bench = [...flat(64), 105]; // +5%
    expect(relativeStrengthBullet("TEST", sym, bench)).toContain(
      "outperformed the S&P 500 by 25%",
    );
    expect(relativeStrengthBullet("TEST", bench, bench)).toBeNull();
    expect(relativeStrengthBullet("TEST", sym, null)).toBeNull();
  });
});

suite("event warnings", () => {
  it("reports earnings and an ex-dividend landing inside the expiration", () => {
    const warnings = eventWarnings(
      "2027-02-19",
      { expected_report_date: "2027-02-01" },
      { dividend_ex_date: "2027-02-10", dividend_rate_per_share: 0.85 },
      TODAY,
    );
    expect(warnings).toHaveLength(2);
    expect(warnings[0]).toContain("earnings report (2027-02-01)");
    expect(warnings[1]).toContain("ex-dividend 2027-02-10 ($0.85/share)");
  });

  it("stays silent for events outside the expiration — absence is a real claim", () => {
    const warnings = eventWarnings(
      "2027-01-15",
      { expected_report_date: "2027-02-01" }, // after expiration
      { dividend_ex_date: "2026-12-10" }, // in the past
      TODAY,
    );
    expect(warnings).toEqual([]);
  });
});
