/**
 * Why is the console not answering?
 *
 * The shell never starts a server — the supervisor owns that process, and a second console would take
 * the port the supervisor's own child needs, after which every supervised restart fails to bind. So
 * when the window has nothing to load, the useful thing this can do is *diagnose*, from the same
 * local files `/console --status` reads. All file-only: no broker, no network beyond one loopback GET.
 */
import fs from "node:fs";
import http from "node:http";
import {
  BIND_HOST,
  consoleHeartbeatPath,
  supervisorHeartbeatPath,
  supervisorJobsPath,
} from "@console/shared";

/** How stale the supervisor's heartbeat may be before it counts as dead. Matches the daemon's own
 *  `HEARTBEAT_FRESH_SECONDS`, which tolerates a slow pass without flapping. */
export const SUPERVISOR_FRESH_SECONDS = 90;

export type ConsoleState = "up" | "starting" | "down";

export interface ConsoleStatus {
  state: ConsoleState;
  /** One sentence naming the cause. */
  headline: string;
  /** The single command that fixes it, when there is one. */
  fix?: string;
}

function ageSeconds(file: string): number | null {
  try {
    return (Date.now() - fs.statSync(file).mtimeMs) / 1000;
  } catch {
    return null;
  }
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** One loopback GET to /api/health. Resolves false rather than throwing on any failure — a refused
 *  connection and a timeout are the same answer here. */
export function probeHealth(port: number, timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(
      { host: BIND_HOST, port, path: "/api/health", timeout: timeoutMs },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

/**
 * Explain a console that is not answering, in the order that distinguishes the causes:
 * nothing supervising it → the job is off → the job is failing → it is coming up.
 */
export function diagnose(home?: string): ConsoleStatus {
  const supervisorAge = ageSeconds(supervisorHeartbeatPath(home));
  if (supervisorAge === null || supervisorAge > SUPERVISOR_FRESH_SECONDS) {
    return {
      state: "down",
      headline:
        supervisorAge === null
          ? "The supervisor is not running, so nothing is keeping the console up."
          : `The supervisor last checked in ${Math.round(supervisorAge)}s ago and looks dead.`,
      fix: "python packages/orchestrator/run.py install",
    };
  }

  const jobs = readJson(supervisorJobsPath(home));
  const job = ((jobs?.["jobs"] as Record<string, unknown>)?.["console"] ?? null) as Record<
    string,
    unknown
  > | null;

  if (job === null) {
    return {
      state: "down",
      headline:
        "The supervisor is running but has no console job — it is probably running older code than this checkout.",
      fix: "restart the supervisor so it re-derives its job table",
    };
  }

  if (job["enabled"] === false) {
    const reason = String(job["enabled_reason"] ?? "disabled");
    const unbuilt = reason.includes("not built");
    return {
      state: "down",
      headline: `The console job is disabled: ${reason}.`,
      fix: unbuilt
        ? "cd packages/console && pnpm install && pnpm build"
        : 'set "console": {"enabled": true} in ~/.cherrypick/config.json',
    };
  }

  const resident = String(job["resident_state"] ?? "");
  if (resident === "backoff") {
    return {
      state: "down",
      headline: "The console is crash-looping, so the supervisor is backing off before it retries.",
      fix: "check ~/.cherrypick/logs/console/console.log — usually another process holds the port",
    };
  }

  // The job is up as far as the supervisor knows, but the port did not answer. Either it is still
  // binding, or it has wedged and the silence check has not fired yet. The heartbeat separates them.
  const beat = ageSeconds(consoleHeartbeatPath(home));
  if (beat !== null && beat > 60) {
    return {
      state: "down",
      headline: `The console is running but has not written its heartbeat for ${Math.round(beat)}s — it is wedged, and the supervisor is about to restart it.`,
    };
  }
  return { state: "starting", headline: "The console is starting up." };
}
