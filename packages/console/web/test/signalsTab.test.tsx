import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { SignalStudyResult } from "@console/shared";
import { distanceLabel, droppedSentence, fmtPct, fmtShare, horizonLabel, horizonSentence, minuteLabel, StudyBody } from "../src/pages/Compare/SignalsTab";

/**
 * Compare → signals (#1179): the module's figures in words and tables. Synthetic figures only; the
 * page computes nothing and labels nothing better or worse.
 */

const side = (n: number, mean: number, up: number) => ({
  n,
  mean_pct: mean,
  median_pct: mean,
  sd_pct: 1.2,
  up_share: up,
  mean_up_pct: 0.8,
  mean_down_pct: 0.6,
  em_n: n,
  mean_range_em: 0.9,
  beyond_em_share: 0.3,
  distances: [{ unit: "pct" as const, value: 2, n, up: 0.25, down: 0.1, either: 0.3 }],
});

const DOC: SignalStudyResult = {
  ok: true,
  name: "gap-up",
  licensed: false,
  request: {
    name: "gap-up",
    signal: { kind: "gap", direction: "up", pct: 1 },
    symbol: "SPY",
    source: "history",
    horizons: [1, 5],
    distances: [{ unit: "pct", value: 2 }],
  },
  em_index: "VIX",
  bars: { count: 100, first: "2011-01-03", last: "2011-05-27", sessions: 100 },
  dropped_sessions: { not_trading_day: 0, incomplete: 0 },
  firings_n: 4,
  firing_sessions: 4,
  control_n: 95,
  horizons: [
    { horizon: 1, fired: side(4, 0.25, 0.75), control: side(95, 0.05, 0.55), difference: { mean_pct: 0.2, t: 1.4 }, incomplete: 0 },
    { horizon: 5, fired: { n: 0 }, control: side(90, 0.3, 0.6), difference: null, incomplete: 4 },
  ],
  by_year: [
    {
      year: "2011",
      horizons: [
        { horizon: 1, fired_n: 4, fired_mean_pct: 0.25, control_n: 95, control_mean_pct: 0.05, difference: { mean_pct: 0.2, t: 1.4 } },
        { horizon: 5, fired_n: 0, fired_mean_pct: null, control_n: 90, control_mean_pct: 0.3, difference: null },
      ],
    },
  ],
  firings: [{ session: "2011-02-01", minute: 960, price: 130.5, value: 1.4, h1: { move_pct: 0.3, up_pct: 0.5, down_pct: 0.2, range_em: 0.6 }, h5: null }],
  source: { store: "technicals/history.db", em_file: "market-files/cboe/VIX.csv" },
};

describe("the signals tab's words", () => {
  it("formats signed percents, shares, horizons, minutes and distances", () => {
    expect(fmtPct(0.123)).toBe("+0.12%");
    expect(fmtPct(-1.5)).toBe("-1.50%");
    expect(fmtPct(null)).toBe("—");
    expect(fmtShare(0.555)).toBe("56%");
    expect(horizonLabel(0)).toBe("to the close");
    expect(horizonLabel(1)).toBe("next close");
    expect(horizonLabel(5)).toBe("5 sessions on");
    expect(minuteLabel(605)).toBe("10:05");
    expect(distanceLabel({ unit: "em", value: 1 })).toBe("1 × expected move");
    expect(distanceLabel({ unit: "points", value: 20 })).toBe("20 points");
  });

  it("puts each horizon in a sentence beside its control, and says when nothing had a full window", () => {
    expect(horizonSentence(DOC.horizons[0]!)).toBe(
      "next close: after 4 firings the price moved +0.25% on average and closed higher 75% of the time, against +0.05% and 55% on 95 control windows (difference +0.20%, t 1.4).",
    );
    expect(horizonSentence(DOC.horizons[1]!)).toBe("5 sessions on: no firing had a full window.");
  });

  it("names only the reasons that left sessions out", () => {
    expect(droppedSentence({ not_trading_day: 0, incomplete: 0 })).toBe("");
    expect(droppedSentence({ not_trading_day: 9, incomplete: 0 })).toBe(" Left out: 9 sessions dated on a day the exchange was shut.");
    expect(droppedSentence({ not_trading_day: 1, incomplete: 2 })).toBe(
      " Left out: 1 session dated on a day the exchange was shut and 2 sessions whose bars stop before the close.",
    );
  });
});

describe("one study", () => {
  it("draws the summary, the horizon, distance and year tables and the firings", () => {
    const html = renderToString(<StudyBody doc={DOC} />).replace(/<!-- -->/g, "");
    expect(html).toContain("gap-up · SPY");
    expect(html).toContain("technicals&#x27; history (public)");
    expect(html).toContain("After each firing, against the control");
    expect(html).toContain("How often a distance was reached");
    expect(html).toContain("By year");
    expect(html).toContain("Every firing (1)");
    expect(html).toContain("priced from VIX");
    expect(html).not.toContain("Licensed");
  });

  it("says a licensed result is licensed", () => {
    const html = renderToString(<StudyBody doc={{ ...DOC, licensed: true, source: { ...DOC.source!, reads_holdout: true } }} />);
    expect(html).toContain("Licensed: computed from the vendor");
    expect(html).toContain("holdout window");
  });
});
