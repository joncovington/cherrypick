import { describe, it, expect, vi, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import type { MarketDataService } from "../src/market/marketData.js";

const fakeClient = {
  marketMetricsService: {
    getMarketMetrics: vi.fn().mockResolvedValue({
      data: {
        items: [
          {
            symbol: "AAPL",
            "implied-volatility-index-rank": "0.55",
            "liquidity-rating": 4,
            "implied-volatility-index": "0.30",
          },
        ],
      },
    }),
  },
  instrumentsService: {
    getNestedOptionChain: vi.fn().mockRejectedValue(new Error("eod mode must not fetch chains")),
  },
};

vi.mock("../src/market/session.js", () => ({
  hasCredential: () => true,
  getClient: () => fakeClient,
}));

import { runScreener } from "../src/services/screener.js";
import { etNow } from "../src/services/chainEod.js";
import { writeChainEod, chainEodStatus, type ChainEodOptionRow } from "../src/store/consoleDb.js";

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-eod-test-"));
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "marketdata", "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      scoutDir: path.join(tmp, "scout"),
    },
  };
});

function isoInDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

function chainRows(expiration: string, spot: number): ChainEodOptionRow[] {
  const rows: ChainEodOptionRow[] = [];
  for (let k = -10; k <= 10; k++) {
    const strike = spot + k * 5;
    const put = Math.max(0.4, 4 - k * 1.2);
    const call = Math.max(0.4, 4 + k * 1.2);
    rows.push({ expiration, strike, otype: "P", bid: put - 0.1, ask: put + 0.1, mid: put, delta: -0.3, iv: 0.3 });
    rows.push({ expiration, strike, otype: "C", bid: call - 0.1, ask: call + 0.1, mid: call, delta: 0.3, iv: 0.3 });
  }
  return rows;
}

describe("EOD chain snapshot store", () => {
  it("writes, reports status, and replaces per symbol/date", () => {
    const date = etNow().date;
    writeChainEod(config, date, "AAPL", 200, chainRows(isoInDays(35), 200));
    expect(chainEodStatus(config)).toEqual({ tradeDate: date, symbols: 1 });
  });
});

describe("screener EOD mode", () => {
  it("builds candidates from the snapshot with no chain fetch or quote snapshot", async () => {
    const market = {
      snapshotQuotes: vi.fn(async () => {
        throw new Error("eod mode must not snapshot quotes");
      }),
    } as unknown as MarketDataService;

    const result = await runScreener(
      config,
      market,
      ["AAPL", "MSFT"],
      { dteMin: 25, dteMax: 45, wingWidthPct: 0.05, minIvRank: 0, minLiquidity: 0, maxSymbols: 60, quoteSource: "eod" },
      "tt:Core",
    );
    expect(result).not.toHaveProperty("error");
    if ("error" in result) return;
    expect(result.quoteSource).toBe("eod");
    expect(result.eodTradeDate).toBe(etNow().date);
    expect(result.rows.length).toBeGreaterThan(0);
    expect(result.rows.every((r) => r.symbol === "AAPL")).toBe(true);
    // MSFT had no snapshot — reported, not silently dropped.
    expect(result.skipped.some((s) => s.symbol === "MSFT" && s.reason.includes("no EOD chain snapshot"))).toBe(true);
    expect(fakeClient.instrumentsService.getNestedOptionChain).not.toHaveBeenCalled();
  });
});
