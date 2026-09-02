/**
 * The supervisor decides whether to restart the console from this file's mtime, so the two things
 * that matter are that it appears at all (creating `state/` if the console is the first thing to
 * run on a fresh home) and that a write failure never propagates into the server.
 */
import { describe, it, expect, vi } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  heartbeatPath,
  writeHeartbeat,
  startHeartbeat,
  HEARTBEAT_INTERVAL_MS,
} from "../src/services/heartbeat.js";

function tmpHome(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "console-heartbeat-test-"));
}

describe("heartbeat", () => {
  it("writes into state/ and creates the directory when it does not exist", () => {
    const home = tmpHome();
    writeHeartbeat(home, 5070);
    const file = heartbeatPath(home);
    expect(fs.existsSync(file)).toBe(true);
    const payload = JSON.parse(fs.readFileSync(file, "utf-8")) as Record<string, unknown>;
    expect(payload["port"]).toBe(5070);
    expect(payload["pid"]).toBe(process.pid);
    expect(typeof payload["ts"]).toBe("string");
  });

  it("rewrites the same file rather than growing it", () => {
    const home = tmpHome();
    writeHeartbeat(home, 5070);
    const first = fs.statSync(heartbeatPath(home)).size;
    writeHeartbeat(home, 5070);
    expect(fs.statSync(heartbeatPath(home)).size).toBe(first);
  });

  it("writes immediately on start, so a just-started console is visible before the first tick", () => {
    const home = tmpHome();
    const stop = startHeartbeat(home, 5070, () => {});
    try {
      expect(fs.existsSync(heartbeatPath(home))).toBe(true);
    } finally {
      stop();
    }
  });

  it("keeps writing on the interval, and stops when told to", () => {
    vi.useFakeTimers();
    const home = tmpHome();
    const stop = startHeartbeat(home, 5070, () => {});
    try {
      const file = heartbeatPath(home);
      fs.rmSync(file);
      vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS);
      expect(fs.existsSync(file)).toBe(true);

      stop();
      fs.rmSync(file);
      vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 3);
      expect(fs.existsSync(file)).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("survives a write failure and warns exactly once per outage", () => {
    vi.useFakeTimers();
    const home = tmpHome();
    const warnings: string[] = [];
    // A path whose parent is a file, not a directory — mkdirSync throws, as it would if the state
    // dir were unwritable.
    fs.writeFileSync(path.join(home, "state"), "not a directory", "utf-8");
    const stop = startHeartbeat(home, 5070, (m) => warnings.push(m));
    try {
      expect(warnings).toHaveLength(1);
      vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 3);
      expect(warnings).toHaveLength(1);
    } finally {
      stop();
      vi.useRealTimers();
    }
  });
});
