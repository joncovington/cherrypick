import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import {
  buildGexProfile,
  candidateExpirations,
  etParts,
  leftoverSymbols,
  LEFTOVER_ROW_SECONDS,
} from "../src/services/gexProfile.js";

/**
 * Which chain the live GEX chart may show, and which of its rows count (2026-09-30).
 *
 * Off-hours the chart used to show whatever chain still had greeks -- usually bwb's extra window,
 * a different expiry -- and in session it summed strikes the producer's window had re-centred away
 * from days earlier. Off-hours it now shows the LAST session's chain, labelled; never a later one.
 */

const ms = (iso: string) => Date.parse(iso);
const IN_SESSION = ms("2026-09-30T15:00:00Z"); // Wed 11:00 EDT
const PRE_OPEN = ms("2026-10-01T12:45:00Z"); // Thu 08:45 EDT
const AFTER_BELL = ms("2026-09-30T22:40:00Z"); // Wed 18:40 EDT
const LATE_EVENING = ms("2026-10-01T01:00:00Z"); // Wed 21:00 EDT -- already Thursday in UTC
const SATURDAY = ms("2026-10-03T16:00:00Z"); // Sat 12:00 EDT

const EXPS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"];

describe("etParts", () => {
  it("reads the ET date, not the UTC one", () => {
    expect(etParts(LATE_EVENING)).toEqual({ date: "2026-09-30", minutes: 21 * 60, weekday: 3 });
  });
});

describe("candidateExpirations", () => {
  it("in session, prefers the nearest expiration on or after today", () => {
    expect(candidateExpirations(EXPS, IN_SESSION)).toEqual({
      mode: "live",
      order: ["2026-09-30", "2026-10-01", "2026-10-02"],
    });
  });

  it("pre-open, shows the last session's chain and never a later expiry", () => {
    const { mode, order } = candidateExpirations(EXPS, PRE_OPEN);
    expect(mode).toBe("last_session");
    expect(order[0]).toBe("2026-09-30");
    expect(order.some((e) => e >= "2026-10-01")).toBe(false);
  });

  it("after the bell, the session that just closed comes first", () => {
    expect(candidateExpirations(EXPS, AFTER_BELL).order[0]).toBe("2026-09-30");
  });

  it("after 20:00 ET it is still that session (the UTC date has already turned over)", () => {
    expect(candidateExpirations(EXPS, LATE_EVENING)).toEqual({
      mode: "last_session",
      order: ["2026-09-30", "2026-09-29"],
    });
  });

  it("on a weekend, the latest past expiration", () => {
    expect(candidateExpirations(EXPS, SATURDAY).order[0]).toBe("2026-10-02");
  });
});

describe("leftoverSymbols", () => {
  it("drops strikes that stopped updating well before the chain's newest row", () => {
    const now = 1_000_000;
    const stamps = new Map([
      [".a", now],
      [".b", now - 30],
      [".c", now - LEFTOVER_ROW_SECONDS - 1],
    ]);
    expect([...leftoverSymbols(stamps)]).toEqual([".c"]);
  });

  it("keeps a chain that is merely old but updated together", () => {
    const old = 5_000;
    expect(leftoverSymbols(new Map([[".a", old], [".b", old - 60]])).size).toBe(0);
  });
});

describe("buildGexProfile", () => {
  let config: ConsoleConfig;
  const chainNow = AFTER_BELL / 1000 - 60;

  beforeAll(() => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-gexsession-"));
    const cache = new Database(path.join(tmp, "s.db"));
    cache.exec(
      "CREATE TABLE stream_chain (streamer_symbol TEXT, expiration TEXT, underlying_symbol TEXT," +
        " data_json TEXT, updated_at REAL);" +
        "CREATE TABLE stream_trades (symbol TEXT, last REAL, volume REAL, updated_at REAL);" +
        "CREATE TABLE stream_greeks (symbol TEXT, gamma REAL, iv REAL, updated_at REAL);" +
        "CREATE TABLE stream_oi (symbol TEXT, open_interest REAL);",
    );
    cache.prepare("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('SPX', 7650, ?)").run(chainNow);
    const chain = cache.prepare(
      "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json) VALUES (?,?,'SPX',?)",
    );
    const greeks = cache.prepare("INSERT INTO stream_greeks (symbol, gamma, iv, updated_at) VALUES (?,?,0.2,?)");
    const oi = cache.prepare("INSERT INTO stream_oi (symbol, open_interest) VALUES (?, 1000)");
    // The 09-30 session chain: two live strikes and one leftover from a morning re-centre.
    // The 10-02 chain: another module's extra window, still streaming -- what off-hours used to show.
    const rows: Array<[string, string, number, string, number]> = [
      [".SPXW260930C7660", "2026-09-30", 7660, "C", chainNow],
      [".SPXW260930P7640", "2026-09-30", 7640, "P", chainNow - 20],
      [".SPXW260930C7950", "2026-09-30", 7950, "C", chainNow - 5 * 3600],
      [".SPXW261002C7700", "2026-10-02", 7700, "C", chainNow],
      [".SPXW261002P7600", "2026-10-02", 7600, "P", chainNow],
    ];
    for (const [sym, exp, strike, type, ts] of rows) {
      chain.run(sym, exp, JSON.stringify({ strike_price: strike, option_type: type, shares_per_contract: 100 }));
      greeks.run(sym, 0.01, ts);
      oi.run(sym);
    }
    cache.close();
    config = {
      paths: { streamCacheDb: path.join(tmp, "s.db"), gexDir: path.join(tmp, "gex") },
    } as unknown as ConsoleConfig;
  });

  it("pre-open, shows the last session's chain labelled, not the later expiry still streaming", () => {
    const out = buildGexProfile(config, "SPX", PRE_OPEN);
    expect(out["ok"]).toBe(true);
    expect(out["expiration"]).toBe("2026-09-30");
    expect(out["sessionMode"]).toBe("last_session");
    expect(out["chainAsOf"]).toBe(chainNow);
  });

  it("drops the leftover strike's gamma from the profile", () => {
    const out = buildGexProfile(config, "SPX", AFTER_BELL);
    expect(out["leftoverRowsDropped"]).toBe(1);
    // The strike stays on the axis (it is listed) but contributes no exposure; the live ones do.
    const series = out["series"] as Array<{ strike: number; net_gex: number }>;
    expect(series.find((r) => r.strike === 7950)?.net_gex).toBe(0);
    expect(series.find((r) => r.strike === 7660)?.net_gex).not.toBe(0);
  });
});
