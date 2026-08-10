import { describe, it, expect, vi, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import type { MarketDataService } from "../src/market/marketData.js";
import { warmCandles } from "../src/services/candleWarm.js";
import { writeOwnCandles, readOwnCandles, type CandleBar } from "../src/store/consoleDb.js";

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-warm-test-"));
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

function bars(n: number): CandleBar[] {
  return Array.from({ length: n }, (_, i) => ({
    t: 1_700_000_000_000 + i * 86_400_000,
    o: 100,
    h: 101,
    l: 99,
    c: 100 + (i % 5),
    v: 1000,
  }));
}

describe("warmCandles", () => {
  it("warms cold symbols, skips fresh ones, records failures, then hits the run floor", async () => {
    // AAA is already fresh: 250 bars backfilled "now".
    writeOwnCandles(config, "AAA", bars(250));

    const backfill = vi.fn(async (symbol: string) => (symbol === "BBB" ? bars(250) : []));
    const market = { backfillDailyCandles: backfill } as unknown as MarketDataService;

    const result = await warmCandles(config, market, ["AAA", "BBB", "CCC"]);
    expect(result).toEqual(
      expect.objectContaining({ requested: 3, warmed: 1, skippedFresh: 1, failed: ["CCC"] }),
    );
    expect(backfill).toHaveBeenCalledTimes(2); // BBB and CCC only, never fresh AAA
    expect(readOwnCandles(config, "BBB").length).toBe(250);

    // Immediate re-run is rejected by the module-level floor.
    const again = await warmCandles(config, market, ["BBB"]);
    expect(again).toHaveProperty("error");
  });
});
