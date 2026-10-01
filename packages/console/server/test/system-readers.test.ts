import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readSystemHealth, readSystemSupervisor } from "../src/readers/system.js";
import { readLogTail } from "../src/readers/logs.js";

/**
 * The System page reads the suite's own files and colours only what they recorded. What matters:
 * a job's state comes from its registry row (a hold, a backoff, a non-zero exit that was not a
 * requested restart), the supervisor is alive only by the suite's own heartbeat rule, a finding's
 * "since" is the watchdog's memory of that SAME status, and log lines from writers in different
 * time zones merge in true time order.
 */

function home(): { config: ConsoleConfig; root: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "console-system-"));
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  fs.mkdirSync(path.join(root, "logs"), { recursive: true });
  const config = {
    port: 0,
    paths: {
      cherrypick: root,
      streamCacheDb: path.join(root, "nope.db"),
      watchdogLast: path.join(root, "state", "watchdog.last.json"),
      orchestratorConfig: path.join(root, "config.json"),
    },
  } as unknown as ConsoleConfig;
  return { config, root };
}

function write(root: string, rel: string, body: unknown): void {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), typeof body === "string" ? body : JSON.stringify(body));
}

const NOW_ISO = () => new Date().toISOString();

describe("supervisor jobs", () => {
  it("each job's state is what its registry row recorded", () => {
    const { config, root } = home();
    const future = Date.now() / 1000 + 600;
    write(root, "state/supervisor-jobs.json", {
      written_at: NOW_ISO(),
      derive_errors: {},
      jobs: {
        ok: { kind: "interval", enabled: true, last_exit_code: 0 },
        requested: { kind: "resident", enabled: true, last_exit_code: 1, last_exit_requested: true, resident_state: "outside window" },
        failing: { kind: "daily", enabled: true, last_exit_code: 1, consecutive_failures: 1 },
        backoff: { kind: "daily", enabled: true, last_exit_code: 1, consecutive_failures: 3, backoff_until: future },
        off: { kind: "interval", enabled: false, enabled_reason: "not armed (no arm record)" },
        stopped: { kind: "resident", enabled: true },
        running: { kind: "resident", enabled: true, running_pid: process.pid },
      },
    });
    write(root, "state/holds.json", { stopped: { by: "run.py stop", at: Date.now() / 1000 } });
    const byId = Object.fromEntries(readSystemSupervisor(config).jobs.map((j) => [j.id, j]));
    expect(byId["ok"]).toMatchObject({ state: "idle", level: "ok" });
    // A requested restart exits non-zero and is not a failure.
    expect(byId["requested"]).toMatchObject({ state: "outside window", level: "ok", lastExitRequested: true });
    expect(byId["failing"]).toMatchObject({ state: "failing", level: "warn" });
    expect(byId["backoff"]).toMatchObject({ state: "backoff", level: "critical" });
    expect(byId["off"]).toMatchObject({ state: "disabled", enabledReason: "not armed (no arm record)" });
    expect(byId["stopped"]).toMatchObject({ state: "held", level: "warn" });
    expect(byId["running"]).toMatchObject({ state: "running", runningPid: process.pid });
  });

  it("the supervisor is alive only with a fresh heartbeat AND its pid present", () => {
    const { config, root } = home();
    write(root, "state/supervisor.last.json", { ts: NOW_ISO(), pid: process.pid, started_at: NOW_ISO(), loop_seq: 9 });
    expect(readSystemSupervisor(config).heartbeat.alive).toBe(true);
    write(root, "state/supervisor.last.json", { ts: new Date(Date.now() - 120_000).toISOString(), pid: process.pid });
    expect(readSystemSupervisor(config).heartbeat.alive).toBe(false);
    // A fresh beat from a pid that is gone is a supervisor that died after writing it.
    write(root, "state/supervisor.last.json", { ts: NOW_ISO(), pid: 2 ** 22 + 12345 });
    expect(readSystemSupervisor(config).heartbeat.alive).toBe(false);
  });
});

describe("health", () => {
  it("a finding's 'since' is the watchdog's memory of the same status, and problems sort first", () => {
    const { config, root } = home();
    write(root, "state/watchdog.last.json", {
      ts: NOW_ISO(),
      overall: "WARN",
      findings: [
        { key: "a.ok", status: "OK", title: "fine", message: "" },
        { key: "b.warn", status: "WARN", title: "stuck", message: "x" },
        { key: "c.warn", status: "WARN", title: "new", message: "y" },
      ],
    });
    write(root, "state/watchdog_state.json", {
      "b.warn": { status: "WARN", first_seen: "2026-09-20T01:00:00+00:00" },
      // Remembered as CRITICAL: a different status, so it says nothing about how long WARN has stood.
      "c.warn": { status: "CRITICAL", first_seen: "2026-09-01T00:00:00+00:00" },
    });
    const h = readSystemHealth(config);
    expect(h.watchdog.findings.map((f) => f.key)).toEqual(["b.warn", "c.warn", "a.ok"]);
    expect(h.watchdog.findings[0]!.since).toBe("2026-09-20T01:00:00+00:00");
    expect(h.watchdog.findings[1]!.since).toBeNull();
    expect(h.checks.find((c) => c.key === "watchdog")?.level).toBe("warn");
  });

  it("a failing job turns the jobs tile, so the page cannot read all-OK over it", () => {
    const { config, root } = home();
    write(root, "state/supervisor-jobs.json", {
      written_at: NOW_ISO(),
      jobs: { "report-edition": { kind: "daily", enabled: true, last_exit_code: 1, consecutive_failures: 1 }, fine: { kind: "interval", enabled: true, last_exit_code: 0 } },
    });
    const jobs = readSystemHealth(config).checks.find((c) => c.key === "jobs")!;
    expect(jobs.level).toBe("warn");
    expect(jobs.lines[0]).toContain("report-edition");
  });

  it("no supervisor heartbeat at all is critical, not unknown", () => {
    const { config } = home();
    expect(readSystemHealth(config).checks.find((c) => c.key === "supervisor")?.level).toBe("critical");
  });
});

describe("log merge", () => {
  it("lines from writers in different zones merge in true time order", () => {
    const { config, root } = home();
    // 07:00:00Z written three ways, a second apart, interleaved by source.
    const local = (iso: string) => {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${String(d.getFullYear())}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    write(root, "logs/watchdog.log", `{"ts": "2026-10-01T07:00:02+00:00", "overall": "OK", "message": "third"}\n`);
    write(root, "logs/supervisor.log", `${local("2026-10-01T07:00:00Z")} first\n`);
    write(root, "logs/console/console.log", `{"level":40,"time":${String(Date.parse("2026-10-01T07:00:01Z"))},"msg":"second"}\n`);
    const merged = [
      ...readLogTail(config, 10, "watchdog"),
      ...readLogTail(config, 10, "supervisor"),
      ...readLogTail(config, 10, "console"),
    ].sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
    expect(merged.map((l) => l.text)).toEqual(["first", "second", "third"]);
    expect(merged[1]!.level).toBe("WARN");
  });
});
