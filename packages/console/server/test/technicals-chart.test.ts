import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readChart, readSetupsWatchlist } from "../src/readers/technicals.js";

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
        { kind: "support", value: 9, date: "2026-09-23", on_our_grid: true, vendor_view: true },
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
    expect(chart?.vendor?.levels.map((l) => l.vendorView)).toEqual([true, null]);
    expect(chart?.vendor?.barsAgree).toBe(12);
  });

  it("drops a bar missing a price and keeps every indicator aligned with the bars kept", () => {
    write("MSFT.json", chartDoc("MSFT"));
    const c = readChart(config, "MSFT").chart;
    expect(c?.bars.map((b) => b.date)).toEqual(["2026-09-23", "2026-09-25"]);
    expect(c?.cci14).toEqual([null, 120]);
    expect(c?.trendShort).toEqual([2, 4]);
  });

  it("passes the setups through and keeps their lines aligned with the bars kept", () => {
    write("SPX.json", {
      ...chartDoc("SPX"),
      chart_version: 3,
      volume_source: "SPY",
      setup_lines: { ema21: [10, 11, 12], supertrend: [null, 9.5, 10] },
      setups: [
        {
          id: "breakout",
          name: "Breakout",
          rule: "Enter on a close above the upper band…",
          lines: ["supertrend"],
          trades: [
            { entry_date: "2026-09-23", entry_price: 10.5, exit_date: "2026-09-25", exit_price: 12.5, reason: "supertrend", target: null },
            { entry_date: "2026-09-25", entry_price: 12.5, exit_date: null, exit_price: null, reason: null, target: null },
            { exit_date: "2026-09-25" },
          ],
        },
        { name: "no id" },
      ],
    });
    const c = readChart(config, "SPX").chart;
    expect(c?.volumeSource).toBe("SPY");
    expect(c?.setupLines).toEqual({ ema21: [10, 12], supertrend: [null, 10] });
    expect(c?.setups.map((s) => s.id)).toEqual(["breakout"]);
    expect(c?.setups[0]?.trades).toEqual([
      { entryDate: "2026-09-23", entryPrice: 10.5, exitDate: "2026-09-25", exitPrice: 12.5, reason: "supertrend", target: null },
      { entryDate: "2026-09-25", entryPrice: 12.5, exitDate: null, exitPrice: null, reason: null, target: null },
    ]);
  });

  it("a chart file from before the setups reads as none, not a failure", () => {
    write("MSFT.json", chartDoc("MSFT"));
    const c = readChart(config, "MSFT").chart;
    expect(c?.setups).toEqual([]);
    expect(c?.setupLines).toEqual({});
    expect(c?.volumeSource).toBeNull();
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

describe("the setups watchlist reader", () => {
  it("passes rows through, drops one missing its identity, and derives open/closed from the exit", () => {
    write("setups-index.json", {
      version: 1,
      session: "2026-10-02",
      window: 20,
      rows: [
        {
          symbol: "HPQ", session: "2026-10-02", setup: "trend", setup_name: "Trend following",
          entry_date: "2026-10-01", entry_price: 32.12, entry_ago: 1, exit_date: null, exit_price: null,
          exit_ago: null, reason: null, target: null, status: "closed", last_close: 32.09, move_pct: -0.09,
          trend_1m: 2, trend_6m: 3, trend_1m_label: "Bullish", trend_6m_label: "Bullish", rs: 10,
          vs_spy_1m: 0.54, support: { value: 30.71, pct: -4.39 }, resistance: null,
        },
        { symbol: "NOID", setup: "trend" },
      ],
    });
    const w = readSetupsWatchlist(config);
    expect(w).toMatchObject({ session: "2026-10-02", window: 20 });
    expect(w.rows).toHaveLength(1);
    expect(w.rows[0]).toMatchObject({ symbol: "HPQ", status: "open", rs: 10, vsSpy1m: 0.54, resistance: null });
    expect(w.rows[0]?.support).toEqual({ value: 30.71, pct: -4.39 });
  });

  it("no file is an empty watchlist, not a failure", () => {
    expect(readSetupsWatchlist(config)).toEqual({ session: null, generatedAt: null, window: null, rows: [] });
  });
});
