/**
 * Where the suite lives, and which port the console listens on.
 *
 * Shared rather than duplicated because the server and the desktop shell have to agree: the shell
 * opens a window on the port the server bound, and both read the same `console.json`. They disagreed
 * once already — the server hardcoded `~/.cherrypick` and ignored `$CHERRYPICK_HOME` — and the
 * failure mode is a shell reporting "not running" at a console that is running perfectly well
 * somewhere else.
 *
 * This mirrors `cherrypick.core.home` on the Python side: `$CHERRYPICK_HOME` is the master override
 * that relocates the whole tree, else `~/.cherrypick`.
 */
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

/** 5060/5061 are on Chrome's unsafe-port list (SIP), which is why the default is not there. */
export const DEFAULT_CONSOLE_PORT = 5070;

/** Loopback only — never configurable. Matches the suite-wide guardrail. */
export const BIND_HOST = "127.0.0.1";

export function cherrypickHome(env: NodeJS.ProcessEnv = process.env): string {
  const override = env["CHERRYPICK_HOME"];
  if (override) {
    const expanded = override.startsWith("~") ? path.join(os.homedir(), override.slice(1)) : override;
    return path.resolve(expanded);
  }
  return path.join(os.homedir(), ".cherrypick");
}

export function consoleConfigPath(home = cherrypickHome()): string {
  return path.join(home, "config", "console.json");
}

/** The console's listen port: `serve.port` in console.json, else the default. Never throws — an
 *  unreadable or malformed config means "use the default", the same as no config at all. */
export function consolePort(home = cherrypickHome()): number {
  try {
    const raw = JSON.parse(fs.readFileSync(consoleConfigPath(home), "utf-8")) as Record<string, unknown>;
    const serve = (raw["serve"] ?? {}) as Record<string, unknown>;
    const port = serve["port"];
    if (typeof port === "number" && Number.isInteger(port) && port > 0 && port < 65536) return port;
  } catch {
    /* no config, bad JSON, no read permission — all mean the default */
  }
  return DEFAULT_CONSOLE_PORT;
}

export function consoleUrl(home = cherrypickHome()): string {
  return `http://${BIND_HOST}:${consolePort(home)}/`;
}

/** The liveness file the console rewrites every ~15s and the supervisor watches. */
export function consoleHeartbeatPath(home = cherrypickHome()): string {
  return path.join(home, "state", "console.heartbeat");
}

/** The supervisor's own heartbeat — how the shell tells "the console is down" from "nothing is
 *  running the console". */
export function supervisorHeartbeatPath(home = cherrypickHome()): string {
  return path.join(home, "state", "supervisor.last.json");
}

/** The supervisor's per-job registry, which carries the `console` job's state and disabled reason. */
export function supervisorJobsPath(home = cherrypickHome()): string {
  return path.join(home, "state", "supervisor-jobs.json");
}

/**
 * The Python interpreter that has the suite installed, for every bridge the server shells out to.
 *
 * Order: `$CHERRYPICK_PYTHON` (the launcher `run.py` sets it to its own interpreter), then the
 * checkout's `.venv` found by walking up from `startDir` (this module's folder) to the folder holding
 * `packages/orchestrator/run.py`, then a bare `python` on PATH. A bare `python` alone failed a
 * console started by hand on Linux, where often only `python3` exists and the venv is on nobody's
 * PATH: no credential, no Config page, no positions (2026-10-08 OS audit).
 */
export function suitePython(
  env: NodeJS.ProcessEnv = process.env,
  // This file's own folder: always inside the checkout, wherever the process was started from.
  startDir: string = path.dirname(fileURLToPath(import.meta.url)),
  exists: (p: string) => boolean = fs.existsSync,
): string {
  const chosen = env["CHERRYPICK_PYTHON"];
  if (chosen) return chosen;
  let dir = path.resolve(startDir);
  for (let i = 0; i < 10; i++) {
    if (exists(path.join(dir, "packages", "orchestrator", "run.py"))) {
      for (const rel of [path.join(".venv", "Scripts", "python.exe"), path.join(".venv", "bin", "python")]) {
        const candidate = path.join(dir, rel);
        if (exists(candidate)) return candidate;
      }
      break;
    }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return "python";
}
