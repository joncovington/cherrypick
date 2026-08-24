import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readSystemPanel } from "../src/services/suite.js";

/**
 * The System card's services table carries the WATCHDOG's verdict for each service (its
 * `service.<id>` finding from the last tick) — the console renders what the watchdog decided and
 * never re-derives service health. The failure this guards against is a reassuring answer that
 * isn't true: a wedged recorder rendering as healthy because only pid/config facts were shown, or
 * an absent watchdog report rendering as "running".
 */

let tmp: string;
let config: ConsoleConfig;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-system-test-"));
  fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
  fs.writeFileSync(
    path.join(tmp, "config.json"),
    JSON.stringify({
      services: [{ id: "gex-recorder", enabled: true, auto_restart: true }],
    }),
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
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  } as ConsoleConfig;
});

function writeWatchdog(findings: Array<Record<string, unknown>>): void {
  fs.writeFileSync(config.paths.watchdogLast, JSON.stringify({ ts: "2026-08-23T20:00:00Z", findings }));
}

describe("services health join", () => {
  it("an OK finding renders as its message ('running')", () => {
    writeWatchdog([{ key: "service.gex-recorder", status: "OK", title: "gex-recorder", message: "running" }]);
    const svc = readSystemPanel(config).services[0];
    expect(svc).toMatchObject({ id: "gex-recorder", health: "OK", note: "running" });
  });

  it("a stall verdict comes through with the id stripped and the full message kept", () => {
    writeWatchdog([
      {
        key: "service.gex-recorder",
        status: "WARN",
        title: "gex-recorder stalled — recycled",
        message: "Process alive but heartbeat silent 400s; stopped and relaunched.",
      },
    ]);
    const svc = readSystemPanel(config).services[0];
    expect(svc.health).toBe("WARN");
    expect(svc.note).toBe("stalled — recycled");
    expect(svc.detail).toContain("heartbeat silent 400s");
  });

  it("no watchdog report is null, never a healthy default", () => {
    const svc = readSystemPanel(config).services[0];
    expect(svc.health).toBeNull();
    expect(svc.note).toBeNull();
  });

  it("findings for other keys never bleed onto a service row", () => {
    writeWatchdog([{ key: "streamer", status: "WARN", title: "Streamer down", message: "stalled" }]);
    const svc = readSystemPanel(config).services[0];
    expect(svc.health).toBeNull();
  });
});
