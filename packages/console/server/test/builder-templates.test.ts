import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { occSymbol, suggestions, incomeGrid } from "../src/services/builderTemplates.js";
import { writeChainEod, type ChainEodOptionRow } from "../src/store/consoleDb.js";

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-tpl-test-"));
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

/** A chain whose deltas spread realistically so delta-targeted picks differ. */
function chainRows(expiration: string, spot: number): ChainEodOptionRow[] {
  const rows: ChainEodOptionRow[] = [];
  for (let k = -10; k <= 10; k++) {
    const strike = spot + k * 5;
    // Put delta walks from ~-0.9 (deep ITM, k>0) to ~-0.1 (far OTM, k<0).
    const putDelta = -Math.min(0.95, Math.max(0.05, 0.5 + k * 0.08));
    const callDelta = 1 + putDelta;
    const put = Math.max(0.3, 4 + k * 1.2);
    const call = Math.max(0.3, 4 - k * 1.2);
    rows.push({ expiration, strike, otype: "P", bid: put - 0.1, ask: put + 0.1, mid: put, delta: putDelta, iv: 0.3 });
    rows.push({ expiration, strike, otype: "C", bid: call - 0.1, ask: call + 0.1, mid: call, delta: callDelta, iv: 0.3 });
  }
  return rows;
}

describe("occSymbol", () => {
  it("builds the standard OCC form", () => {
    expect(occSymbol("F", "2026-09-18", "P", 12.5)).toBe("F     260918P00012500");
    expect(occSymbol("AAPL", "2026-09-18", "C", 200)).toBe("AAPL  260918C00200000");
  });
});

describe("suggestions + income grid from the EOD snapshot", () => {
  it("builds sentiment cards with payoff numbers and OCC legs", () => {
    const trades = new Date().toISOString().slice(0, 10);
    writeChainEod(config, trades, "AAPL", 200, [
      ...chainRows(isoInDays(35), 200),
      ...chainRows(isoInDays(63), 200),
    ]);
    const result = suggestions(config, "AAPL", "bullish");
    expect(result).not.toHaveProperty("error");
    if ("error" in result) return;
    expect(result.cards.length).toBeGreaterThan(0);
    const names = result.cards.map((c) => c.name);
    expect(names).toContain("put_vertical_credit");
    for (const card of result.cards) {
      expect(card.legs.every((l) => l.occSymbol !== null)).toBe(true);
      expect(card.pop).not.toBeNull();
    }
  });

  it("rejects unknown sentiments and missing snapshots", () => {
    expect(suggestions(config, "AAPL", "sideways")).toHaveProperty("error");
    expect(suggestions(config, "ZZZZ", "bullish")).toHaveProperty("error");
  });

  it("fills grid tiers at distinct delta targets with POW", () => {
    const result = incomeGrid(config, "AAPL", "put");
    expect(result).not.toHaveProperty("error");
    if ("error" in result) return;
    expect(result.buckets.length).toBeGreaterThan(0);
    const bucket = result.buckets[0]!;
    const { conservative, optimal, aggressive } = bucket.tiers;
    expect(conservative!.strike).toBeLessThan(optimal!.strike);
    expect(optimal!.strike).toBeLessThan(aggressive!.strike);
    expect(conservative!.pow).toBeGreaterThan(aggressive!.pow!);
  });
});
