/**
 * The flies order-alert daemon's health, off the files it writes itself: its pid file and its
 * status JSON (`data/flies/live_alert_daemon.{pid,status.json}`). Read-only; the console never
 * starts or stops it -- `/live-flies-start` does.
 *
 * The same two checks `cherrypick.flies.alert_daemon --status` makes: is the pid-file process alive,
 * and is its heartbeat fresh. The daemon rewrites `heartbeat_at` after every listen slice
 * (`LISTEN_SLICE_SECONDS`, 30), so a live process whose heartbeat is three slices old is the
 * silently-dead-websocket case that flag exists to show.
 *
 * Health is informational, never a gate: the daemon only makes fills get noticed sooner, and a dead
 * one costs latency and nothing else -- the loop still confirms fills on its own poll.
 */
import fs from "node:fs";
import path from "node:path";
import type { AlertDaemonHealth } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readJson } from "../readers/db.js";
import { readLockStatus } from "./liveLock.js";

const LISTEN_SLICE_SECONDS = 30;
export const STALE_AFTER_SECONDS = 3 * LISTEN_SLICE_SECONDS;

/** Is `pid` a live process? Signal 0 tests existence without delivering anything, on Windows too.
 *  EPERM means it exists and belongs to someone else -- alive. */
export function pidAlive(pid: number | null): boolean {
  if (pid === null || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

function readPid(file: string): number | null {
  try {
    const n = Number.parseInt(fs.readFileSync(file, "utf-8").trim(), 10);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

/**
 * Null when the daemon is not part of the setup (`live.use_order_alert_daemon` off): there is
 * nothing to be healthy. Otherwise one of:
 *   ok    -- running, heartbeat fresh;
 *   stale -- running, but no heartbeat for three slices;
 *   down  -- not running while flies is armed for today;
 *   off   -- not running and not armed, which is its normal state outside an armed day.
 */
export function readAlertDaemonHealth(
  config: ConsoleConfig,
  now: number = Date.now(),
  alive: (pid: number | null) => boolean = pidAlive,
): AlertDaemonHealth | null {
  const fliesCfg = readJson(config.paths.fliesConfig) ?? {};
  const live = (fliesCfg["live"] ?? {}) as Record<string, unknown>;
  if (live["use_order_alert_daemon"] !== true) return null;

  const pid = readPid(path.join(config.paths.fliesDir, "live_alert_daemon.pid"));
  const status = readJson(path.join(config.paths.fliesDir, "live_alert_daemon.status.json")) ?? {};
  const heartbeatAt = typeof status["heartbeat_at"] === "string" ? status["heartbeat_at"] : null;
  const beat = heartbeatAt !== null ? Date.parse(heartbeatAt) : Number.NaN;
  const ageSeconds = Number.isFinite(beat) ? Math.max(0, Math.round((now - beat) / 1000)) : null;
  const running = alive(pid);
  const armed = readLockStatus(config).fliesArm.armed;

  const state: AlertDaemonHealth["state"] = running
    ? ageSeconds !== null && ageSeconds <= STALE_AFTER_SECONDS
      ? "ok"
      : "stale"
    : armed
      ? "down"
      : "off";
  return {
    state,
    armed,
    pid: running ? pid : null,
    heartbeatAt,
    ageSeconds,
    alertsSeen: typeof status["alerts_seen"] === "number" ? status["alerts_seen"] : null,
    lastAlertAt: typeof status["last_alert_at"] === "string" ? status["last_alert_at"] : null,
  };
}
