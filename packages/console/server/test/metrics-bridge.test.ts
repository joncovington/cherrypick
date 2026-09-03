import { describe, it, expect, afterEach } from "vitest";
import {
  readModuleMetrics,
  resetMetricsCache,
  setMetricsCaller,
} from "../src/services/metricsBridge.js";

/**
 * The console asks `core.metrics` for a schema's calibration reading rather than re-deriving it --
 * `calibration_reading` is ~20 metric functions over a normalised ledger read, the same bundle
 * `calibrate` promotes on, and a TypeScript second implementation would be free to drift from it
 * (the `services/report.ts` mistake, cited in the bridge's own docstring).
 *
 * What these tests pin is the TypeScript-side part: the shape handed on, the memoisation the
 * page's polling depends on, and that BOTH a spawn failure and the CLI's own {"ok": false} report
 * as an error rather than an empty reading.
 */

afterEach(() => {
  setMetricsCaller();
  resetMetricsCache();
});

const READING = {
  ok: true,
  error: null,
  metrics: {
    schema: "meic_ic",
    n_records: 2,
    groups: {
      control: {
        reading: { sample: 2, net_pnl: 50.0, capture_rate: { value: 0.4, n: 2 } },
        session_nets: [["2026-08-20", 115.0], ["2026-08-21", -65.0]] as Array<[string, number]>,
        trade_nets: [115.0, -65.0],
      },
    },
  },
};

describe("the metrics bridge", () => {
  it("passes the reading through with snake_case field names intact", () => {
    setMetricsCaller(() => READING);
    const out = readModuleMetrics("/db/meic.db", "meic_ic", null, null, 1_000);
    expect(out.ok).toBe(true);
    expect(out.metrics?.n_records).toBe(2);
    expect(out.metrics?.groups["control"]?.reading["net_pnl"]).toBe(50.0);
    expect(out.metrics?.groups["control"]?.session_nets).toEqual([
      ["2026-08-20", 115.0],
      ["2026-08-21", -65.0],
    ]);
  });

  it("passes --start/--end through to the subprocess only when given", () => {
    const seen: unknown[] = [];
    setMetricsCaller((dbPath, schema, start, end) => {
      seen.push([dbPath, schema, start, end]);
      return READING;
    });
    readModuleMetrics("/db/meic.db", "meic_ic", "2026-08-01", "2026-08-31", 1_000);
    expect(seen).toEqual([["/db/meic.db", "meic_ic", "2026-08-01", "2026-08-31"]]);
  });

  it("reports the CLI's own {ok:false} refusal as an error, never an empty reading", () => {
    // core.metrics read returns {"ok": false, "error": "unknown schema ..."} for a bad schema or
    // an unreadable db, per its own "never silent" contract -- an empty groups object here would
    // read as "no profiles traded", which is a finding and must never come from a refused read.
    setMetricsCaller(() => ({ ok: false, metrics: null, error: "unknown schema 'not_a_schema'" }));
    const out = readModuleMetrics("/db/x.db", "not_a_schema", null, null, 1_000);
    expect(out.ok).toBe(false);
    expect(out.metrics).toBeNull();
    expect(out.error).toContain("unknown schema");
  });

  it("memoises per (db, schema, start, end), because a reading replays the whole ledger", () => {
    let calls = 0;
    setMetricsCaller(() => {
      calls += 1;
      return READING;
    });
    readModuleMetrics("/db/meic.db", "meic_ic", null, null, 1_000);
    readModuleMetrics("/db/meic.db", "meic_ic", null, null, 60_000);
    expect(calls).toBe(1);
    // Past the TTL it asks again -- a new closed trade since then would change the reading.
    readModuleMetrics("/db/meic.db", "meic_ic", null, null, 1_000 + 200_000);
    expect(calls).toBe(2);
  });

  it("memoises different schemas and date ranges under separate keys", () => {
    let calls = 0;
    setMetricsCaller(() => {
      calls += 1;
      return READING;
    });
    readModuleMetrics("/db/meic.db", "meic_ic", null, null, 1_000);
    readModuleMetrics("/db/curve.db", "curve_vx", null, null, 1_000);
    readModuleMetrics("/db/meic.db", "meic_ic", "2026-08-01", null, 1_000);
    expect(calls).toBe(3);
  });
});
