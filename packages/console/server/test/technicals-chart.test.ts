import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readChart } from "../src/readers/technicals.js";

/**
 * The chart reader passes the technicals package's chart file through. What it must not do: let a
 * request name a file outside the charts directory, draw a bar with a missing price at zero, or let
 * the indicator arrays slide out of line with the bars when one is dropped.
 */

let tmp: string;
let config: ConsoleConfig;

function charts(): string {
  return path.join(tmp, "technicals", "charts");
}

function write(name: string, doc: unknown): void {
  fs.writeFileSync(path.join(charts(), name), typeof doc === "string" ? doc : JSON.stringify(doc));
}

function chartDoc(symbol: string): Record<string, unknown> {
  return {
    ok: true,
    chart_version: 1,
    symbol,
    session: "2026-09-25",
    bars: {
      date: ["2026-09-23", "2026-09-24", "2026-09-25"],
      open: [10, 11, 12],
      high: [11, null, 13],
      low: [9, 10, 11],
      close: [10.5, 11.5, 12.5],
      volume: [100, 200, 300],
    },
    grid: { low: 9, high: 13, step: 0.05, low_date: "2026-09-23", high_date: "2026-09-25", window: 250 },
    cci14: [null, 50, 120],
    cci5: [1, 2, 3],
    rsi14: [null, null, 60],
    trend_short: [2, 3, 4],
    trend_long: [null, null, 4],
    signals: [{ date: "2026-09-25", rules: ["CciDipInBullishTrend"] }],
    vendor: {
      capture: "2026-09-25",
      through: "2026-09-25",
      levels: [
        { kind: "support", value: 9, date: "2026-09-23", on_our_grid: true },
        { kind: "gapResistance", value: 14.2, date: "2026-09-01", on_our_grid: null },
      ],
      rank: 7,
      trend_short: [{ date: "2026-09-25", value: 4 }],
      bars_compared: 12,
      bars_agree: 12,
    },
    record_only: true,
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-chart-test-"));
  fs.mkdirSync(charts(), { recursive: true });
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
});

describe("the chart reader", () => {
  it("rebuilds rows from the columnar file and passes the vendor block through", () => {
    write("MSFT.json", chartDoc("MSFT"));
    write("index.json", { session: "2026-09-25", symbols: [{ symbol: "MSFT", vendor: true }] });
    const { index, chart } = readChart(config, "msft");
    expect(index.symbols).toEqual([{ symbol: "MSFT", vendor: true }]);
    expect(chart?.grid).toMatchObject({ low: 9, high: 13, step: 0.05 });
    expect(chart?.vendor?.levels.map((l) => l.onOurGrid)).toEqual([true, null]);
    expect(chart?.vendor?.barsAgree).toBe(12);
  });

  it("drops a bar missing a price and keeps every indicator aligned with the bars kept", () => {
    write("MSFT.json", chartDoc("MSFT"));
    const c = readChart(config, "MSFT").chart;
    expect(c?.bars.map((b) => b.date)).toEqual(["2026-09-23", "2026-09-25"]);
    expect(c?.cci14).toEqual([null, 120]);
    expect(c?.trendShort).toEqual([2, 4]);
  });

  it("refuses a symbol that could name a file outside the charts directory", () => {
    fs.writeFileSync(path.join(tmp, "technicals", "secret.json"), JSON.stringify(chartDoc("SECRET")));
    for (const s of ["../secret", "..\\secret", "A/B", "", "index"]) {
      expect(readChart(config, s).chart).toBeNull();
    }
  });

  it("no file, a failed file or an unreadable one is null, and an absent index is empty", () => {
    expect(readChart(config, "NOPE")).toEqual({ index: { session: null, symbols: [] }, chart: null });
    write("BAD.json", "{not json");
    expect(readChart(config, "BAD").chart).toBeNull();
    write("OFF.json", { ...chartDoc("OFF"), ok: false });
    expect(readChart(config, "OFF").chart).toBeNull();
  });
});
