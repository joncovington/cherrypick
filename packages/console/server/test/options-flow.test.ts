import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { listFlowSessions, readOptionsFlow } from "../src/readers/optionsFlow.js";

/**
 * The Options flow reader passes the capture through. What it guards against is the quiet failure:
 * a rejected capture shown as a day, a cell that did not read becoming 0, a derived field
 * recomputed here instead of read, and an absent store looking like a day with no flow.
 */

let tmp: string;
let config: ConsoleConfig;
let dir: string;

function write(name: string, doc: unknown): void {
  fs.writeFileSync(path.join(dir, name), JSON.stringify(doc));
}

function capture(session: string): Record<string, unknown> {
  return {
    session,
    saved_at: `${session}T20:52:00+00:00`,
    tables: {
      birdseye: [
        {
          symbol: "TSLA", name: "Tesla, Inc.", buckets: { "1s": 421800, "=>1K": 57 }, bands: { "1": 421800, "100+": 3040 },
          calls: 462900, puts: 299600, total: 762500, call_share: 0.607, shown: { "1s": "421.8K", total: "762.5K" },
        },
      ],
      outrights: [
        {
          symbol: "PCG", name: "PG&E Corporation", time_et: "13:38:45.517", size: 40900, expires: "2027-01-15", strike: 16,
          cp: "call", price: 0.37, premium: 1513300, premium_derived: true, side: { sentiment: "Bullish", fill: "On Ask", edge: 1 },
        },
      ],
      sweeps: [{ symbol: "SKHY", size: 70, price: null, premium: 646450, side: null, cp: "x" }],
      spreads: [
        { symbol: "AI", time_et: "15:01:34.327", size: 41900, price: 0.47, delta: 0.24, premium: 1969300, direction: "bought", group: "AI 15:01:34.327 41900", underlying: { last: 11.12, bid: 11.11, ask: 11.12 } },
        { symbol: "AI", time_et: "15:01:34.327", size: 41900, price: -0.22, delta: -0.24, premium: -921800, direction: "sold", group: "AI 15:01:34.327 41900" },
      ],
      voloi: [{ symbol: "SPCX", volume: 135071, oi: 119, v_oi: 1135.05 }],
      openings: [{ symbol: "AXGN", volume: 11105, oi: 0, v_oi: 11105 }],
    },
    derived: {
      version: 1,
      names: [{ symbol: "TSLA", name: "Tesla, Inc.", tables: ["birdseye", "voloi"] }],
      premium_by_side: { Bullish: 1513300, Neutral: 646450 },
      trades_by_side: { Bullish: 1, Neutral: 1 },
    },
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flow-test-"));
  dir = path.join(tmp, "quikoptions", "hot-options");
  config = { port: 0, paths: { cherrypick: tmp, quikoptionsDir: path.join(tmp, "quikoptions") } } as unknown as ConsoleConfig;
});

describe("options flow reader", () => {
  it("an absent store says so, rather than reading as a day with no flow", () => {
    const payload = readOptionsFlow(config);
    expect(payload.current).toBeNull();
    expect(payload.sessions).toEqual([]);
    expect(payload.degraded?.reason).toMatch(/no capture yet/);
  });

  it("lists passing captures only, and serves the latest unless asked", () => {
    fs.mkdirSync(dir, { recursive: true });
    write("2026-10-01.json", capture("2026-10-01"));
    write("2026-10-02.json", capture("2026-10-02"));
    write("2026-10-05.rejected.json", capture("2026-10-05"));
    write("2026-10-05.rejected.html", "");
    expect(listFlowSessions(config)).toEqual(["2026-10-01", "2026-10-02"]);
    expect(readOptionsFlow(config).current?.session).toBe("2026-10-02");
    expect(readOptionsFlow(config, "2026-10-01").current?.session).toBe("2026-10-01");
    expect(readOptionsFlow(config, "2026-10-05").current?.session).toBe("2026-10-02"); // rejected: not a day
  });

  it("passes the capture's derived fields through and never turns a missing cell into 0", () => {
    fs.mkdirSync(dir, { recursive: true });
    write("2026-10-02.json", capture("2026-10-02"));
    const day = readOptionsFlow(config).current!;
    expect(day.birdseye[0]).toMatchObject({ symbol: "TSLA", callShare: 0.607, shown: { "1s": "421.8K", total: "762.5K" } });
    expect(day.outrights[0]).toMatchObject({ premium: 1513300, premiumDerived: true, cp: "call", side: { sentiment: "Bullish" } });
    expect(day.sweeps[0]).toMatchObject({ price: null, side: null, cp: null, timeEt: null, premiumDerived: false });
    expect(day.spreads.map((s) => [s.direction, s.group])).toEqual([
      ["bought", "AI 15:01:34.327 41900"],
      ["sold", "AI 15:01:34.327 41900"],
    ]);
    expect(day.spreads[1]?.underlying).toBeNull();
    expect(day.openings[0]).toMatchObject({ oi: 0, vOi: 11105 });
    expect(day.names).toEqual([{ symbol: "TSLA", name: "Tesla, Inc.", tables: ["birdseye", "voloi"] }]);
    expect(day.premiumBySide).toEqual({ Bullish: 1513300, Neutral: 646450 });
    expect(day.tradesBySide).toEqual({ Bullish: 1, Neutral: 1 });
  });

  it("an unreadable capture is degraded, not an empty day", () => {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "2026-10-02.json"), "{ not json");
    const payload = readOptionsFlow(config);
    expect(payload.current).toBeNull();
    expect(payload.degraded?.reason).toMatch(/could not be read/);
  });
});
