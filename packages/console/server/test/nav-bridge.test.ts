import { describe, expect, it } from "vitest";
import { normalizeContango, normalizeCurve } from "../src/services/navBridge.js";

/** The bridge is the one place snake_case becomes camelCase. These are the field names
 *  `core.metrics.nav` and the two modules' analytics write; a rename on either side fails here. */
describe("navBridge normalisation", () => {
  it("maps a contango metrics response, monthly keys untouched", () => {
    const out = normalizeContango({
      arms: {
        control: {
          starting_capital: 10000,
          reading: { basis: "daily", days: 3, max_drawdown: -0.02, min_track_record_days: null, monthly: { "2026-10": 0.01 } },
          series: [["2026-10-06", 9998.0], ["2026-10-07", 10100.5]],
          expected: [["2026-10-06", 10000.0]],
          expected_reading: { basis: "daily", days: 0 },
          tracking: -0.0012,
        },
      },
      benchmarks: { SVXY: { series: [["2026-10-06", 10000]], reading: { basis: "daily", days: 0 } } },
    });
    const c = out.arms["control"]!;
    expect(c.startingCapital).toBe(10000);
    expect(c.reading.maxDrawdown).toBe(-0.02);
    expect(c.reading.minTrackRecordDays).toBeNull();
    expect(c.reading.monthly).toEqual({ "2026-10": 0.01 });
    expect(c.series).toHaveLength(2);
    expect(c.tracking).toBe(-0.0012);
    expect(out.benchmarks["SVXY"]!.series).toEqual([["2026-10-06", 10000]]);
  });

  it("maps a curve equity response", () => {
    const out = normalizeCurve({
      arms: { control: { series: [["2026-09-02", 1.02]], carried: 2, reading: { basis: "daily", days: 1, worst_day: 0, drawdown_span: { longest: 0, open: 0 } } } },
    });
    expect(out.arms["control"]!.carried).toBe(2);
    expect(out.arms["control"]!.reading.worstDay).toBe(0);
    expect(out.arms["control"]!.reading.drawdownSpan).toEqual({ longest: 0, open: 0 });
  });
});
