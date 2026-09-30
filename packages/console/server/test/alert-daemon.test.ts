import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { pidAlive, readAlertDaemonHealth, STALE_AFTER_SECONDS } from "../src/services/alertDaemon.js";
import { sessionDateEt } from "../src/services/liveLock.js";

/**
 * The flies order-alert daemon's health, read off its own pid and status files. Pinned: the four
 * states (ok / stale / down / off) against a live-or-dead pid and a fresh-or-old heartbeat, "down"
 * only when flies is armed for today, and no reading at all when the daemon is not configured.
 */

let root: string;
let config: ConsoleConfig;
const NOW = Date.parse("2026-09-30T12:00:00-04:00");

function cfg(useDaemon: boolean): ConsoleConfig {
  fs.mkdirSync(path.join(root, "flies"), { recursive: true });
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ modules: { flies: {} } }));
  fs.writeFileSync(path.join(root, "config", "flies.json"), JSON.stringify({ live: { enabled: true, use_order_alert_daemon: useDaemon } }));
  return {
    port: 0,
    paths: {
      cherrypick: root,
      orchestratorConfig: path.join(root, "config.json"),
      fliesDir: path.join(root, "flies"),
      fliesConfig: path.join(root, "config", "flies.json"),
      meicRiskConfig: path.join(root, "config", "meic-risk.json"),
    },
  } as unknown as ConsoleConfig;
}

function daemon(pid: number | null, heartbeatSecondsAgo: number | null) {
  const dir = path.join(root, "flies");
  if (pid !== null) fs.writeFileSync(path.join(dir, "live_alert_daemon.pid"), String(pid));
  const beat = heartbeatSecondsAgo === null ? null : new Date(NOW - heartbeatSecondsAgo * 1000).toISOString();
  fs.writeFileSync(
    path.join(dir, "live_alert_daemon.status.json"),
    JSON.stringify({ connected_since: beat, alerts_seen: 4, last_alert_at: null, heartbeat_at: beat }),
  );
}

function arm(date: string) {
  fs.writeFileSync(path.join(root, "state", "flies-live-arm.json"), JSON.stringify({ date, at: `${date}T01:43:51-04:00` }));
}

const alive = (live: number) => (pid: number | null) => pid === live;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "console-alertd-"));
  config = cfg(true);
});

describe("the order-alert daemon's health", () => {
  it("is ok while the process lives and the heartbeat is inside three slices", () => {
    daemon(4242, 20);
    const h = readAlertDaemonHealth(config, NOW, alive(4242))!;
    expect(h).toMatchObject({ state: "ok", pid: 4242, ageSeconds: 20, alertsSeen: 4 });
  });

  it("is stale when the process lives but the heartbeat stopped -- the silent-websocket case", () => {
    daemon(4242, STALE_AFTER_SECONDS + 1);
    expect(readAlertDaemonHealth(config, NOW, alive(4242))!.state).toBe("stale");
  });

  it("is down when the process is gone on an armed day, and off when nothing is armed", () => {
    daemon(4242, 5700); // the 2026-09-30 case: heartbeat stopped at 02:33 with nothing in the log
    expect(readAlertDaemonHealth(config, NOW, alive(9999))).toMatchObject({ state: "off", armed: false, pid: null });
    arm(sessionDateEt());
    expect(readAlertDaemonHealth(config, NOW, alive(9999))).toMatchObject({ state: "down", armed: true });
    arm("2020-01-02"); // yesterday's record is a gate the loop already shut
    expect(readAlertDaemonHealth(config, NOW, alive(9999))!.state).toBe("off");
  });

  it("reads a missing pid file or status as not running, never as healthy", () => {
    arm(sessionDateEt());
    expect(readAlertDaemonHealth(config, NOW, pidAlive)).toMatchObject({ state: "down", pid: null, heartbeatAt: null });
  });

  it("is absent when the daemon is not part of the setup", () => {
    config = cfg(false);
    daemon(4242, 5);
    expect(readAlertDaemonHealth(config, NOW, alive(4242))).toBeNull();
  });

  it("probes a real pid: this process is alive, an absurd one is not", () => {
    expect(pidAlive(process.pid)).toBe(true);
    expect(pidAlive(2 ** 30)).toBe(false);
    expect(pidAlive(null)).toBe(false);
  });
});
