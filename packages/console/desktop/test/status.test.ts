/**
 * The shell's whole value when the console is down is telling the four causes apart. Getting this
 * wrong is worse than a browser error page, because a confident wrong answer sends you to fix
 * something that was never broken.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { diagnose, SUPERVISOR_FRESH_SECONDS } from "../src/status.js";

function home(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-desktop-test-"));
  fs.mkdirSync(path.join(dir, "state"), { recursive: true });
  return dir;
}

function write(dir: string, rel: string, body: unknown, ageSeconds = 0): void {
  const file = path.join(dir, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, typeof body === "string" ? body : JSON.stringify(body), "utf-8");
  if (ageSeconds > 0) {
    const t = (Date.now() - ageSeconds * 1000) / 1000;
    fs.utimesSync(file, t, t);
  }
}

/** A supervisor that is alive and running the console job normally. */
function healthy(dir: string, job: Record<string, unknown> = {}): void {
  write(dir, "state/supervisor.last.json", { pid: 1 });
  write(dir, "state/supervisor-jobs.json", {
    jobs: { console: { enabled: true, resident_state: "running", running_pid: 2, ...job } },
  });
}

describe("diagnose", () => {
  it("blames the supervisor when it has never run", () => {
    const dir = home();
    const s = diagnose(dir);
    expect(s.state).toBe("down");
    expect(s.headline).toContain("supervisor is not running");
    expect(s.fix).toContain("install");
  });

  it("blames the supervisor when its heartbeat has gone stale", () => {
    const dir = home();
    write(dir, "state/supervisor.last.json", { pid: 1 }, SUPERVISOR_FRESH_SECONDS + 30);
    const s = diagnose(dir);
    expect(s.state).toBe("down");
    expect(s.headline).toContain("looks dead");
  });

  it("names an unbuilt checkout and gives the build command", () => {
    const dir = home();
    healthy(dir, { enabled: false, enabled_reason: "console not built (packages/console: pnpm ...)" });
    const s = diagnose(dir);
    expect(s.state).toBe("down");
    expect(s.headline).toContain("not built");
    expect(s.fix).toContain("pnpm build");
  });

  it("distinguishes turned-off-on-purpose from unbuilt", () => {
    const dir = home();
    healthy(dir, { enabled: false, enabled_reason: "disabled in config (console)" });
    const s = diagnose(dir);
    expect(s.fix).toContain('"enabled": true');
    expect(s.fix).not.toContain("pnpm");
  });

  it("points at the log when the job is crash-looping", () => {
    const dir = home();
    healthy(dir, { resident_state: "backoff" });
    const s = diagnose(dir);
    expect(s.headline).toContain("crash-looping");
    expect(s.fix).toContain("console.log");
  });

  it("calls a running job with a stale heartbeat a wedge, not a start-up", () => {
    const dir = home();
    healthy(dir);
    write(dir, "state/console.heartbeat", { pid: 2 }, 300);
    const s = diagnose(dir);
    expect(s.state).toBe("down");
    expect(s.headline).toContain("wedged");
  });

  it("reports a fresh heartbeat with an unanswered port as still starting", () => {
    const dir = home();
    healthy(dir);
    write(dir, "state/console.heartbeat", { pid: 2 });
    expect(diagnose(dir).state).toBe("starting");
  });

  it("treats a missing heartbeat under a healthy job as starting, not wedged", () => {
    // The window can open in the seconds between spawn and first heartbeat; calling that a wedge
    // would tell the user something is broken every time they launch the app.
    const dir = home();
    healthy(dir);
    expect(diagnose(dir).state).toBe("starting");
  });

  it("says so when the supervisor is alive but has no console job at all", () => {
    const dir = home();
    write(dir, "state/supervisor.last.json", { pid: 1 });
    write(dir, "state/supervisor-jobs.json", { jobs: { watchdog: {} } });
    const s = diagnose(dir);
    expect(s.state).toBe("down");
    expect(s.headline).toContain("no console job");
  });

  it("survives a corrupt job registry rather than failing to render", () => {
    const dir = home();
    write(dir, "state/supervisor.last.json", { pid: 1 });
    write(dir, "state/supervisor-jobs.json", "{ not json");
    expect(() => diagnose(dir)).not.toThrow();
    expect(diagnose(dir).state).toBe("down");
  });
});
