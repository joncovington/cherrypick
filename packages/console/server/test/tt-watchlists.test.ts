import { describe, it, expect, vi, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";

vi.mock("../src/market/session.js", () => ({
  hasCredential: () => true,
  getClient: () => {
    throw new Error("tests must inject deps, not build a real client");
  },
}));

import {
  parseEntries,
  refreshTtWatchlists,
  resolveSource,
  ttWatchlistPayload,
  metricsFor,
} from "../src/services/ttWatchlists.js";
import { eventWarnings } from "../src/analytics/narrative.js";
import {
  addToWatchlist,
  getTtWatchlist,
  listTtWatchlists,
  setPublicPin,
  addToBlacklist,
  getBlacklistReason,
  removeFromBlacklist,
  readTtMetrics,
} from "../src/store/consoleDb.js";

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-tt-test-"));
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

const userList = (name: string, symbols: string[]) => ({
  name,
  "watchlist-entries": symbols.map((s) => ({ symbol: s, "instrument-type": "Equity" })),
});

describe("parseEntries", () => {
  it("keeps equities, drops futures and malformed symbols, reports them", () => {
    const { symbols, skipped } = parseEntries({
      "watchlist-entries": [
        { symbol: "AAPL", "instrument-type": "Equity" },
        { symbol: "SPY", "instrument-type": "ETF" },
        { symbol: "/ESZ6", "instrument-type": "Future" },
        { symbol: "BRK.B", "instrument-type": "Equity" },
        { symbol: "AAPL", "instrument-type": "Equity" },
        { symbol: "toolongsymbol123", "instrument-type": "Equity" },
      ],
    });
    expect(symbols).toEqual(["AAPL", "SPY", "BRK.B"]);
    expect(skipped).toEqual(["/ESZ6", "TOOLONGSYMBOL123"]);
  });

  it("tolerates entries with no instrument-type", () => {
    const { symbols } = parseEntries({ "watchlist-entries": [{ symbol: "MSFT" }] });
    expect(symbols).toEqual(["MSFT"]);
  });
});

describe("refreshTtWatchlists", () => {
  it("caches user watchlists and honors the TTL", async () => {
    const deps = {
      getAllWatchlists: vi.fn().mockResolvedValue({ items: [userList("Core", ["AAPL", "MSFT"])] }),
      getPublicWatchlist: vi.fn(),
    };
    await refreshTtWatchlists(config, { now: 1000, deps });
    expect(getTtWatchlist(config, "tt:Core")?.symbols).toEqual(["AAPL", "MSFT"]);

    // Inside the TTL nothing is refetched.
    await refreshTtWatchlists(config, { now: 1100, deps });
    expect(deps.getAllWatchlists).toHaveBeenCalledTimes(1);

    // After the TTL it is.
    await refreshTtWatchlists(config, { now: 2000, deps });
    expect(deps.getAllWatchlists).toHaveBeenCalledTimes(2);
  });

  it("stale-serves the cache when the broker call fails", async () => {
    const deps = {
      getAllWatchlists: vi.fn().mockRejectedValue(new Error("boom")),
      getPublicWatchlist: vi.fn(),
    };
    await refreshTtWatchlists(config, { now: 5000, force: true, deps });
    expect(getTtWatchlist(config, "tt:Core")?.symbols).toEqual(["AAPL", "MSFT"]);
  });

  it("fetches pinned public lists only", async () => {
    setPublicPin(config, "Liquid Symbols", true);
    const deps = {
      getAllWatchlists: vi.fn().mockResolvedValue({ items: [] }),
      getPublicWatchlist: vi.fn().mockResolvedValue(userList("Liquid Symbols", ["SPY", "QQQ"])),
    };
    await refreshTtWatchlists(config, { now: 9000, force: true, deps });
    expect(deps.getPublicWatchlist).toHaveBeenCalledWith("Liquid Symbols");
    expect(getTtWatchlist(config, "public:Liquid Symbols")?.symbols).toEqual(["SPY", "QQQ"]);
  });
});

describe("resolveSource", () => {
  it("resolves local, cached tt/public keys, and rejects unknowns", () => {
    addToWatchlist(config, "TSLA");
    expect(resolveSource(config, "local")).toContain("TSLA");
    expect(resolveSource(config, "tt:Core")).toEqual(["AAPL", "MSFT"]);
    expect(resolveSource(config, "public:Liquid Symbols")).toEqual(["SPY", "QQQ"]);
    expect(resolveSource(config, "tt:Nope")).toBeNull();
    expect(resolveSource(config, "garbage")).toBeNull();
  });
});

describe("ttWatchlistPayload", () => {
  it("builds rows without broker calls and flags small lists live", async () => {
    const payload = await ttWatchlistPayload(config, "tt:Core");
    expect(payload).not.toBeNull();
    expect(payload!.live).toBe(true);
    expect(payload!.rows.map((r) => r.symbol)).toEqual(["AAPL", "MSFT"]);
    // No stream cache or candles in the temp dir — nulls, not throws.
    expect(payload!.rows[0]!.last).toBeNull();
    expect(payload!.rows[0]!.eodClose).toBeNull();
    expect(listTtWatchlists(config).length).toBeGreaterThan(0);
  });
});

describe("symbol blacklist", () => {
  it("round-trips add/get/remove", () => {
    addToBlacklist(config, "XYZ", "no weekly options");
    expect(getBlacklistReason(config, "XYZ")).toBe("no weekly options");
    expect(removeFromBlacklist(config, "XYZ")).toBe(true);
    expect(getBlacklistReason(config, "XYZ")).toBeNull();
  });
});

describe("dividend dates reach the event warnings", () => {
  // The gap this closes: `eventWarnings` was always handed null for its metrics info, so the
  // ex-dividend clause could never fire. In that function absence of a warning is a REAL claim, so
  // a short ITM call held over an ex-date read as "nothing to flag" rather than as "not checked".
  const item = (over: Record<string, unknown> = {}) => ({
    symbol: "SCHD",
    "implied-volatility-index-rank": 0.4,
    "dividend-ex-date": "2027-03-11",
    "dividend-next-date": "2027-06-10",
    "dividend-rate-per-share": "0.77",
    ...over,
  });

  it("stores the dates the metrics response already carried", async () => {
    await metricsFor(config, ["SCHD"], {
      now: 1_000,
      deps: { getMarketMetrics: async () => [item()] },
    });
    const row = readTtMetrics(config, ["SCHD"]).get("SCHD");
    expect(row?.dividendExDate).toBe("2027-03-11");
    expect(row?.dividendNextDate).toBe("2027-06-10");
    expect(row?.dividendRate).toBe(0.77);
  });

  it("drops a date it cannot parse rather than storing a value the warning cannot read", async () => {
    await metricsFor(config, ["JUNK"], {
      now: 1_000,
      deps: {
        getMarketMetrics: async () => [
          item({ symbol: "JUNK", "dividend-ex-date": "soon", "dividend-next-date": "" }),
        ],
      },
    });
    const row = readTtMetrics(config, ["JUNK"]).get("JUNK");
    expect(row?.dividendExDate).toBeNull();
    expect(row?.dividendNextDate).toBeNull();
  });

  it("fires the ex-dividend warning end to end for an expiration spanning the ex-date", async () => {
    await metricsFor(config, ["SCHD"], {
      now: 2_000,
      deps: { getMarketMetrics: async () => [item()] },
    });
    const row = readTtMetrics(config, ["SCHD"]).get("SCHD");
    const warnings = eventWarnings(
      "2027-03-19",
      null,
      {
        dividend_ex_date: row?.dividendExDate,
        dividend_next_date: row?.dividendNextDate,
        dividend_rate_per_share: row?.dividendRate,
      },
      "2027-03-01",
    );
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("ex-dividend 2027-03-11 ($0.77/share)");
  });
});
