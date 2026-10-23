import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { occToStreamer, readManageLive } from "../src/readers/manageLive.js";

/**
 * Live marks re-price the newest evaluation's legs from the stream cache and judge nothing. The
 * guards: the basis, fees and exit levels are the evaluation's; a trade with one stale or missing
 * quote reads null, never part-marked; the book's greeks are beta-weighted with the evaluation's betas.
 */

let tmp: string;
let config: ConsoleConfig;
const NOW = 1_800_000_000_000;

function evalDoc(): Record<string, unknown> {
  return {
    session: "2026-10-22",
    accounts: [
      {
        account: "****1234",
        portfolio: { benchmark: "SPY", underlyings: [{ underlying: "GLW", beta: 2 }] },
        trades: [
          {
            trade_id: "GLW",
            underlying: "GLW",
            cost: -800,
            fees: -4,
            pnl_gross: -263.5,
            capture: -0.329375,
            exit_levels: { take_profit: { close_value: -400 }, stop: { close_value: -1600 } },
            legs: [
              { symbol: "GLW   261120C00155000", right: "C", quantity: 1, mid: 1.41, value: 141 },
              { symbol: "GLW   261120C00150000", right: "C", quantity: -1, mid: 1.97, value: -197 },
              { symbol: "GLW   261120P00112500", right: "P", quantity: -2, mid: 2.5, value: -500 },
            ],
          },
          {
            trade_id: "KO",
            underlying: "KO",
            cost: 253,
            fees: null,
            pnl_gross: -147,
            capture: -0.58,
            legs: [{ symbol: "KO    261120P00087500", right: "P", quantity: 1, mid: 1.22, value: 122 }],
          },
        ],
      },
    ],
  };
}

function cache(quotes: Array<[string, number, number, number]>, greeks: Array<[string, number, number]>): void {
  const db = new Database(path.join(tmp, "stream_cache.db"));
  db.exec("CREATE TABLE stream_quotes (symbol TEXT PRIMARY KEY, bid REAL, ask REAL, mid REAL, bid_size REAL, ask_size REAL, updated_at REAL NOT NULL)");
  db.exec("CREATE TABLE stream_greeks (symbol TEXT PRIMARY KEY, delta REAL, gamma REAL, theta REAL, vega REAL, rho REAL, iv REAL, price REAL, updated_at REAL NOT NULL)");
  const q = db.prepare("INSERT INTO stream_quotes VALUES (?, ?, ?, ?, 1, 1, ?)");
  for (const [s, bid, ask, ageS] of quotes) q.run(s, bid, ask, (bid + ask) / 2, NOW / 1000 - ageS);
  const g = db.prepare("INSERT INTO stream_greeks VALUES (?, ?, 0, ?, 0, 0, 0, 0, ?)");
  for (const [s, delta, theta] of greeks) g.run(s, delta, theta, NOW / 1000 - 5);
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "manage-live-"));
  fs.mkdirSync(path.join(tmp, "manage"));
  fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-22.json"), JSON.stringify(evalDoc()));
  config = { paths: { manageDir: path.join(tmp, "manage"), streamCacheDb: path.join(tmp, "stream_cache.db") } } as unknown as ConsoleConfig;
});

describe("occToStreamer", () => {
  it("keys the cache as the core converter does", () => {
    expect(occToStreamer("GLW   261120C00155000")).toBe(".GLW261120C155");
    expect(occToStreamer("GLW   261120P00112500")).toBe(".GLW261120P112.5");
    expect(occToStreamer("KO")).toBe("");
  });
});

describe("readManageLive", () => {
  it("re-marks a trade on the evaluation's basis and flags its take-profit level", () => {
    cache(
      [
        [".GLW261120C155", 0.5, 0.7, 5],
        [".GLW261120C150", 0.9, 1.1, 5],
        [".GLW261120P112.5", 0.9, 1.1, 5],
        ["GLW", 129.9, 130.1, 5],
        ["SPY", 649.9, 650.1, 5],
      ],
      [
        [".GLW261120C155", 0.2, -0.01],
        [".GLW261120C150", 0.3, -0.02],
        [".GLW261120P112.5", -0.1, -0.03],
      ],
    );
    const live = readManageLive(config, NOW);
    const glw = live.trades.find((t) => t.tradeId === "GLW")!;
    // 0.6×100 − 1.0×100 − 2×1.0×100 = −240; against −800 paid in: +560 gross, +556 net, 70% kept.
    expect(glw.value).toBeCloseTo(-240);
    expect(glw.pnlGross).toBeCloseTo(560);
    expect(glw.pnlNet).toBeCloseTo(556);
    expect(glw.capture).toBeCloseTo(0.7);
    expect(glw.atTakeProfit).toBe(true);
    expect(glw.atStop).toBe(false);
    expect(live.spots).toEqual({ GLW: 130, SPY: 650 });
  });

  it("leaves a trade with an unquoted leg null, never part-marked", () => {
    cache([[".GLW261120C155", 0.5, 0.7, 5]], []);
    const live = readManageLive(config, NOW);
    const glw = live.trades.find((t) => t.tradeId === "GLW")!;
    expect(glw.value).toBeNull();
    expect(glw.pnlGross).toBeNull();
    expect(glw.legsQuoted).toBe(1);
    expect(glw.legsTotal).toBe(3);
  });

  it("does not use a quote older than the cutoff", () => {
    cache([[".KO261120P87.5", 1.0, 1.2, 600]], []);
    expect(readManageLive(config, NOW).trades.find((t) => t.tradeId === "KO")!.value).toBeNull();
  });

  it("beta-weights the book's delta with the evaluation's betas, and nulls it while a leg has no greeks", () => {
    cache(
      [
        ["GLW", 129.9, 130.1, 5],
        ["SPY", 649.9, 650.1, 5],
        ["KO", 69.9, 70.1, 5],
      ],
      [
        [".GLW261120C155", 0.2, -0.01],
        [".GLW261120C150", 0.3, -0.02],
        [".GLW261120P112.5", -0.1, -0.03],
      ],
    );
    const a = readManageLive(config, NOW).accounts[0]!;
    expect(a.legsWithGreeks).toBe(3);
    expect(a.legsTotal).toBe(4);
    expect(a.bwd).toBeNull(); // KO's put has no greeks
    expect(a.theta).toBeNull();
  });

  it("is an empty payload when there is no evaluation or no cache", () => {
    const live = readManageLive(config, NOW);
    expect(live.trades.every((t) => t.value === null)).toBe(true);
    fs.rmSync(path.join(tmp, "manage"), { recursive: true });
    expect(readManageLive(config, NOW).session).toBeNull();
  });
});
