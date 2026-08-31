import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readLockStatus, readModuleGate, sessionDateEt, resetLockCaches } from "../src/services/liveLock.js";

/**
 * The lock hero reads three separate gates and must not blur them: the suite halt flag, each
 * module's own live switch, and flies' per-day arm record. The failure this guards against is a
 * reassuring answer that isn't true — an unreadable config rendering as "off", or yesterday's arm
 * record rendering as "armed".
 */

let tmp: string;
let config: ConsoleConfig;

function writeModuleConfig(id: string, doc: unknown): void {
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config", `${id}.json`), JSON.stringify(doc, null, 2));
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-lock-test-"));
  fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
  fs.writeFileSync(
    path.join(tmp, "config.json"),
    JSON.stringify({ modules: { meic: { enabled: true }, flies: { enabled: true }, earnings: { enabled: false } } }),
  );
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
  resetLockCaches();
});

describe("module live gates", () => {
  it("reads the top-level switch meic and earnings use", () => {
    writeModuleConfig("meic", { enable_live_trading: true });
    expect(readModuleGate(config, "meic")).toMatchObject({ liveEnabled: true });
  });

  it("reads flies' nested switch too — a top-level-only read reports an armed module as paper", () => {
    writeModuleConfig("flies", { live: { enabled: true, gate0_confirmed: "jon 2026-07-30: authorized" } });
    expect(readModuleGate(config, "flies")).toMatchObject({ liveEnabled: true, gate0Confirmed: true });
  });

  it("an empty attestation is not a confirmed one", () => {
    writeModuleConfig("flies", { live: { enabled: false, gate0_confirmed: "   " } });
    expect(readModuleGate(config, "flies")).toMatchObject({ liveEnabled: false, gate0Confirmed: false });
  });

  it("an unreadable config is unknown, never a reassuring off", () => {
    expect(readModuleGate(config, "meic").liveEnabled).toBeNull();
    writeModuleConfig("earnings", "not an object");
    expect(readModuleGate(config, "earnings").liveEnabled).toBeNull();
  });

  it("calendars and pmcc carry the same nested live.enabled placeholder as flies", () => {
    writeModuleConfig("calendars", { live: { enabled: false } });
    expect(readModuleGate(config, "calendars")).toMatchObject({ liveEnabled: false });
    writeModuleConfig("pmcc", { live: { enabled: false } });
    expect(readModuleGate(config, "pmcc")).toMatchObject({ liveEnabled: false });
  });
});

describe("the halt flag", () => {
  it("presence is the whole signal — contents are never read", () => {
    expect(readLockStatus(config).halted).toBe(false);
    fs.writeFileSync(path.join(tmp, "state", "halt-live.flag"), "");
    expect(readLockStatus(config).halted).toBe(true);
  });
});

describe("flies' per-day arm record", () => {
  it("today's record is armed", () => {
    fs.writeFileSync(
      path.join(tmp, "state", "flies-live-arm.json"),
      JSON.stringify({ date: sessionDateEt(), at: "2026-08-13T09:20:00-04:00" }),
    );
    expect(readLockStatus(config).fliesArm).toMatchObject({ armed: true, stale: false });
  });

  it("a previous day's record is stale, not armed — the live loop self-disarms on it", () => {
    fs.writeFileSync(path.join(tmp, "state", "flies-live-arm.json"), JSON.stringify({ date: "2026-08-11" }));
    expect(readLockStatus(config).fliesArm).toMatchObject({ armed: false, stale: true, date: "2026-08-11" });
  });

  it("no record at all is simply not armed", () => {
    expect(readLockStatus(config).fliesArm).toMatchObject({ armed: false, stale: false, date: null });
  });
});

describe("the full status", () => {
  it("lists every configured module, enabled or not", () => {
    writeModuleConfig("meic", { enable_live_trading: false });
    const status = readLockStatus(config);
    expect(status.modules.map((m) => m.id)).toEqual(["meic", "flies", "earnings"]);
    expect(status.sessionDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
