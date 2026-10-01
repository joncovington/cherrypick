import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readFuturesTicker } from "../src/readers/futures.js";

/**
 * The futures ticker's contract lookup. The symbol is only ever the broker's answer from
 * `state/futures_contracts.json`: a missing map, a stale one, or a product the map lacks is a gap
 * with a reason, never a guessed symbol and never a contract that may have rolled.
 */

const NOW = new Date("2026-10-01T12:00:00Z");

function withMap(map: unknown | null): ConsoleConfig {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-futures-"));
  if (map !== null) {
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    fs.writeFileSync(path.join(tmp, "state", "futures_contracts.json"), JSON.stringify(map));
  }
  return { port: 0, paths: { cherrypick: tmp } } as unknown as ConsoleConfig;
}

const CONTRACTS = {
  ES: [{ symbol: "/ESZ6", streamer_symbol: "/ESZ26:XCME", expiration: "2026-12-18", active_month: true }],
  NQ: [{ symbol: "/NQZ6", streamer_symbol: "/NQZ26:XCME", expiration: "2026-12-18", active_month: true }],
  CL: [{ symbol: "/CLX6", streamer_symbol: "/CLX26:XNYM", expiration: "2026-10-20", active_month: true }],
  GC: [{ symbol: "/GCZ6", streamer_symbol: "/GCZ26:XCEC", expiration: "2026-12-29", active_month: true }],
};

describe("futures ticker contracts", () => {
  it("resolves each product from the map, in ticker order", () => {
    const t = readFuturesTicker(withMap({ refreshed_at: "2026-10-01T06:48:30+00:00", contracts: CONTRACTS }), NOW);
    expect(t.entries.map((e) => e.label)).toEqual(["/ES", "/NQ", "/CL", "/GC", "/ZB"]);
    expect(t.entries[0]).toMatchObject({ streamerSymbol: "/ESZ26:XCME", contract: "/ESZ6", reason: null });
    expect(t.entries[3]).toMatchObject({ streamerSymbol: "/GCZ26:XCEC", reason: null });
  });

  it("a product the map does not carry is a gap, not a guessed symbol", () => {
    const zb = readFuturesTicker(withMap({ refreshed_at: "2026-10-01T06:48:30+00:00", contracts: CONTRACTS }), NOW)
      .entries[4]!;
    expect(zb).toMatchObject({ product: "ZB", streamerSymbol: null, reason: "not_in_map" });
  });

  it("a stale map quotes nothing, because its contracts may have rolled", () => {
    const t = readFuturesTicker(withMap({ refreshed_at: "2026-09-20T06:00:00+00:00", contracts: CONTRACTS }), NOW);
    expect(t.entries.every((e) => e.streamerSymbol === null && e.reason === "map_stale")).toBe(true);
  });

  it("no map at all is a gap for every product", () => {
    const t = readFuturesTicker(withMap(null), NOW);
    expect(t.refreshedAt).toBeNull();
    expect(t.entries.every((e) => e.streamerSymbol === null && e.reason === "no_map")).toBe(true);
  });
});
