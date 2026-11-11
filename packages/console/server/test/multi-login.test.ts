import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { meicLiveAccounts, meicLiveBlock, meicLiveDbPath, readMeicLive } from "../src/readers/meicLive.js";
import { instanceCachePath, readManageLive } from "../src/readers/manageLive.js";
import { readLiveHandAll } from "../src/readers/liveHand.js";
import { readAccountTimeSpreads } from "../src/readers/timeSpreads.js";
import { logSources } from "../src/readers/logs.js";
import { brokerLogins, streamerInstances } from "../src/readers/logins.js";
import { readManageModel, setManageModelCaller } from "../src/services/manageModelBridge.js";

/**
 * More than one tastytrade login (`broker.logins`): every console surface that reads an account, a
 * ledger, a cache or a live account reads each login's own, and with one login reads exactly what
 * it always did. Fixtures hold two accounts with the SAME masked number on two logins -- the case
 * every key has to keep apart.
 */

const NOON = new Date("2026-11-11T17:00:00Z"); // 12:00 ET

function home(): { cfg: ConsoleConfig; root: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "multi-login-"));
  for (const d of ["data/meic", "data/manage", "data/marketdata", "state", "config", "logs"]) {
    fs.mkdirSync(path.join(root, d), { recursive: true });
  }
  const cfg = {
    paths: {
      cherrypick: root,
      meicDir: path.join(root, "data", "meic"),
      manageDir: path.join(root, "data", "manage"),
      streamCacheDb: path.join(root, "data", "marketdata", "stream_cache.db"),
    },
  } as unknown as ConsoleConfig;
  return { cfg, root };
}

function write(root: string, rel: string, doc: unknown): void {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), JSON.stringify(doc), "utf-8");
}

function suite(root: string, extra: Record<string, unknown> = {}): void {
  write(root, "config.json", { broker: { logins: [{ name: "margin", label: "Reg T", manage: true }] }, ...extra });
}

function cache(file: string, quotes: Array<[string, number, number]>, now: number): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new Database(file);
  db.exec("CREATE TABLE stream_quotes (symbol TEXT, bid REAL, ask REAL, mid REAL, updated_at REAL)");
  db.exec("CREATE TABLE stream_trades (symbol TEXT, last REAL, updated_at REAL)");
  db.exec("CREATE TABLE stream_greeks (symbol TEXT, delta REAL, theta REAL, updated_at REAL)");
  for (const [s, bid, ask] of quotes) db.prepare("INSERT INTO stream_quotes VALUES (?, ?, ?, ?, ?)").run(s, bid, ask, (bid + ask) / 2, now / 1000);
  db.close();
}

const evalAccount = (login: string, underlying: string, extra: Record<string, unknown> = {}) => ({
  account: "****1234",
  login,
  login_label: login === "main" ? null : "Reg T",
  account_key: `${login}-1234`,
  role: login === "main" ? "live" : "managed",
  designated: login === "main",
  net_liq: 1000,
  portfolio: { benchmark: "SPY" },
  trades: [{ trade_id: `${underlying}-1`, underlying, legs: [{ symbol: underlying, quantity: 10, value: 100, prev_close: 9 }] }],
  ...extra,
});

describe("broker logins and producer instances", () => {
  it("reads the logins and only listed instances", () => {
    const { cfg, root } = home();
    suite(root, { streamer: { enabled: true, instances: ["margin", "ghost"] } });
    expect(brokerLogins(cfg)).toEqual(["margin"]);
    expect(streamerInstances(cfg)).toEqual(["margin"]);
  });
  it("is empty with one login", () => {
    const { cfg } = home();
    expect(brokerLogins(cfg)).toEqual([]);
    expect(streamerInstances(cfg)).toEqual([]);
  });
});

describe("manage's live marks", () => {
  it("quote each account from its own login's producer when it has one", () => {
    const { cfg, root } = home();
    const now = NOON.getTime();
    cache(cfg.paths.streamCacheDb, [["AAA", 10, 10.2], ["SPY", 500, 500.2]], now);
    cache(instanceCachePath(cfg, "margin"), [["BBB", 20, 20.2]], now);
    write(root, "data/manage/eval-2026-11-11.json", {
      session: "2026-11-11",
      stream_instances: ["margin"],
      accounts: [evalAccount("main", "AAA"), evalAccount("margin", "BBB")],
    });
    const p = readManageLive(cfg, now, false);
    const [main, margin] = p.accounts;
    expect(main!.account).toBe("****1234");
    expect(margin!.account).toBe("margin ****1234");
    expect(main!.legsMarked).toBe(1);
    expect(margin!.legsMarked).toBe(1);
  });
  it("never borrow main's quote for an instance account's leg", () => {
    const { cfg, root } = home();
    const now = NOON.getTime();
    cache(cfg.paths.streamCacheDb, [["BBB", 20, 20.2]], now); // main has it, margin's own does not
    write(root, "data/manage/eval-2026-11-11.json", {
      session: "2026-11-11",
      stream_instances: ["margin"],
      accounts: [evalAccount("margin", "BBB")],
    });
    expect(readManageLive(cfg, now, false).accounts[0]!.legsMarked).toBe(0);
  });
  it("read everything from the shared cache with no instance, as before", () => {
    const { cfg, root } = home();
    const now = NOON.getTime();
    cache(cfg.paths.streamCacheDb, [["AAA", 10, 10.2], ["BBB", 20, 20.2]], now);
    write(root, "data/manage/eval-2026-11-11.json", {
      session: "2026-11-11",
      accounts: [evalAccount("main", "AAA"), evalAccount("margin", "BBB")],
    });
    expect(readManageLive(cfg, now, false).accounts.map((a) => a.legsMarked)).toEqual([1, 1]);
  });
});

describe("the hand 0DTE view", () => {
  it("is one per account, each named", () => {
    const { cfg, root } = home();
    const z = { read: true, underlyings: ["SPX"], orders: [], open_legs: [], unreconciled: [] };
    write(root, "data/manage/eval-2026-11-11.json", {
      session: "2026-11-11",
      accounts: [evalAccount("main", "AAA", { zero_dte: z }), evalAccount("margin", "BBB", { zero_dte: z })],
    });
    const hands = readLiveHandAll(cfg, "2026-11-11");
    expect(hands.map((h) => h.account)).toEqual(["****1234", "margin ****1234"]);
    expect(hands.every((h) => h.status === "ok")).toBe(true);
  });
});

describe("MEIC's live accounts", () => {
  const meicCfg = {
    enable_live_trading: true,
    live: { symbol: "SPX", disarm_time: "17:00", arm: "pm-200" },
    live_accounts: { margin: { enabled: false, symbol: "XSP", arm: "pm-200" }, ghost: { enabled: true } },
  };
  it("list only an entry on a listed login that is enabled or has a ledger", () => {
    const { cfg, root } = home();
    suite(root);
    write(root, "config/meic.json", meicCfg);
    expect(meicLiveAccounts(cfg)).toEqual([]); // off and never traded
    fs.mkdirSync(path.dirname(meicLiveDbPath(cfg, "margin")), { recursive: true });
    fs.writeFileSync(meicLiveDbPath(cfg, "margin"), "");
    expect(meicLiveAccounts(cfg)).toEqual([]); // an empty file is not a book
    write(root, "config/meic.json", { ...meicCfg, live_accounts: { margin: { ...meicCfg.live_accounts.margin, enabled: true } } });
    expect(meicLiveAccounts(cfg)).toEqual(["margin"]);
  });
  it("read each account's own ledger, arm record and block, with the timing inherited", () => {
    const { cfg, root } = home();
    suite(root);
    write(root, "config/meic.json", { ...meicCfg, live_accounts: { margin: { ...meicCfg.live_accounts.margin, enabled: true } } });
    write(root, "state/meic.margin-live-arm.json", { date: "2026-11-11", at: "2026-11-11T09:00:00-05:00" });
    expect(meicLiveDbPath(cfg, "margin")).toBe(path.join(cfg.paths.meicDir, "margin", "meic_trades.db"));
    expect(meicLiveBlock({ live: { disarm_time: "16:30" }, live_accounts: { margin: { symbol: "XSP" } } }, "margin")).toEqual({
      symbol: "XSP",
      disarm_time: "16:30",
    });
    const main = readMeicLive(cfg, NOON);
    const margin = readMeicLive(cfg, NOON, "margin");
    expect(main.arm.armed).toBe(false);
    expect(margin.arm.armed).toBe(true);
    expect(margin.arm.symbol).toBe("XSP");
    expect(margin.login).toBe("margin");
    expect(main.accounts).toEqual(["main", "margin"]);
  });
  it("is live-enabled only with the master switch and its own enabled", () => {
    const { cfg, root } = home();
    suite(root);
    write(root, "config/meic.json", { ...meicCfg, live_accounts: { margin: { ...meicCfg.live_accounts.margin, enabled: true } } });
    expect(readMeicLive(cfg, NOON, "margin").arm.liveEnabled).toBe(true);
    write(root, "config/meic.json", { ...meicCfg, enable_live_trading: false, live_accounts: { margin: { enabled: true } } });
    expect(readMeicLive(cfg, NOON, "margin").arm.liveEnabled).toBe(false);
  });
});

describe("the System page's logs", () => {
  it("list each live account's and each producer's own", () => {
    const { cfg, root } = home();
    suite(root, { streamer: { enabled: true, instances: ["margin"] } });
    write(root, "config/meic.json", { live_accounts: { margin: { enabled: true } } });
    const ids = logSources(cfg).map((s) => s.id);
    expect(ids).toEqual(expect.arrayContaining(["meic-live", "meic-live.margin", "meic-alerts.margin", "streamer.margin"]));
  });
});

describe("time spreads", () => {
  it("size to the designated account and name it when there are several", () => {
    const { cfg, root } = home();
    const ts = { session: "2026-11-11", rows: [] };
    write(root, "data/manage/eval-2026-11-11.json", {
      accounts: [
        { ...evalAccount("margin", "BBB"), portfolio: { time_spreads: ts } },
        { ...evalAccount("main", "AAA"), portfolio: { time_spreads: ts } },
      ],
    });
    expect(readAccountTimeSpreads(cfg, "2026-11-11").accountName).toBe("****1234");
  });
});

describe("model a trade", () => {
  afterEach(() => setManageModelCaller());
  it("passes the account and refuses a malformed one", async () => {
    let argv: string[] = [];
    setManageModelCaller(async (a: string[]) => {
      argv = a;
      return { ok: true, json: { ok: true }, error: null };
    });
    await readManageModel("SPY", ["-1 2026-12-18 46P @ 1.05"], "margin-4222");
    expect(argv.slice(-2)).toEqual(["--account", "margin-4222"]);
    const bad = await readManageModel("SPY", ["-1 2026-12-18 46P @ 1.05"], "../x");
    expect(bad.ok).toBe(false);
  });
});
