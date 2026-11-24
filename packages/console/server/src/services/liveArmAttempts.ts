import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { ArmAttempt, ArmableLoop } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readArmedToday, runArm, type CommandOutcome } from "./liveArmingBridge.js";

/**
 * An arm, run by the server and not by the request, so the person may leave the page.
 *
 * The POST that confirms returns 202 with an attempt id; this file does the rest: the module's arm
 * command, then the check the slash command ends with (the loop's own status says armed today, and for
 * MEIC the supervisor has enabled `meic-live` with a future `next_run`). Its state is a small file under
 * the console's data folder, so a page left open, a second tab or a restarted console all read the same
 * answer, and the shell's notice fires on whichever page the person is on.
 *
 * What a failure says is what a fresh `--status` found, never what this file assumed: once the module's
 * command has answered ok the record exists, and an attempt whose supervisor check ran out of time says
 * so and says the loop IS armed. A restart while an attempt runs resolves it by reading the status; the
 * arm command is never run twice.
 */

const KEEP = 20;
const NOTICE_WINDOW_MS = 10 * 60_000;

export interface AttemptDeps {
  now: () => Date;
  sleep: (ms: number) => Promise<void>;
  /** How long the supervisor gets to enable the job after the record is written. */
  waitMs: number;
  pollMs: number;
}

const realDeps: AttemptDeps = {
  now: () => new Date(),
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  waitMs: 60_000,
  pollMs: 3_000,
};
let deps: AttemptDeps = realDeps;

export function setAttemptDeps(d?: Partial<AttemptDeps>): void {
  deps = d === undefined ? realDeps : { ...realDeps, ...d };
}

const file = (config: ConsoleConfig): string => path.join(config.paths.consoleData, "live-arming.json");

const cache = new Map<string, ArmAttempt[]>();

function load(config: ConsoleConfig): ArmAttempt[] {
  const p = file(config);
  const hit = cache.get(p);
  if (hit !== undefined) return hit;
  let rows: ArmAttempt[] = [];
  try {
    const raw = JSON.parse(fs.readFileSync(p, "utf-8")) as { attempts?: unknown };
    if (Array.isArray(raw.attempts)) rows = raw.attempts as ArmAttempt[];
  } catch {
    // none yet, or unreadable: no attempts
  }
  cache.set(p, rows);
  return rows;
}

function save(config: ConsoleConfig): void {
  const p = file(config);
  const rows = load(config);
  rows.splice(0, Math.max(0, rows.length - KEEP));
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const tmp = `${p}.${String(process.pid)}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ attempts: rows }, null, 2));
    fs.renameSync(tmp, p);
  } catch {
    // The in-memory copy still answers this process; a failed write costs only a restart's memory.
  }
}

/** Drop the in-memory copy. Tests only. */
export function resetAttempts(): void {
  cache.clear();
}

export function listAttempts(config: ConsoleConfig): ArmAttempt[] {
  return [...load(config)];
}

export function activeAttempt(config: ConsoleConfig, loop: ArmableLoop): ArmAttempt | null {
  return load(config).find((a) => a.loop === loop && a.state === "running") ?? null;
}

/** The attempts a notice may still be owed for: finished in the last ten minutes. */
export function recentFinished(config: ConsoleConfig, now = Date.now()): ArmAttempt[] {
  return load(config).filter((a) => a.finishedAt !== null && now - Date.parse(a.finishedAt) < NOTICE_WINDOW_MS);
}

function update(config: ConsoleConfig, a: ArmAttempt, patch: Partial<ArmAttempt>): void {
  Object.assign(a, patch);
  save(config);
}

export interface SupervisorJob {
  enabled: boolean;
  nextRun: string | null;
}

/** The supervisor's registry row for a job, from `state/supervisor-jobs.json`; null when there is none. */
export function readJob(config: ConsoleConfig, id: string): SupervisorJob | null {
  try {
    const reg = JSON.parse(fs.readFileSync(path.join(config.paths.cherrypick, "state", "supervisor-jobs.json"), "utf-8")) as Record<string, unknown>;
    const jobs = reg["jobs"];
    const row = typeof jobs === "object" && jobs !== null ? (jobs as Record<string, unknown>)[id] : undefined;
    if (typeof row !== "object" || row === null) return null;
    const r = row as Record<string, unknown>;
    return { enabled: r["enabled"] !== false, nextRun: typeof r["next_run"] === "string" ? r["next_run"] : null };
  } catch {
    return null;
  }
}

/** The slash command's check: the job is enabled and its next run is still ahead. */
export function jobTicking(job: SupervisorJob | null, now: number): boolean {
  if (job === null || !job.enabled || job.nextRun === null) return false;
  const t = Date.parse(job.nextRun);
  return !Number.isNaN(t) && t > now;
}

const needsSupervisor = (loop: ArmableLoop): boolean => loop === "meic";
const JOB_OF: Record<ArmableLoop, string | null> = { meic: "meic-live", ladder: null };

/** Open an attempt, or hand back the one already running for this loop. */
export function beginAttempt(config: ConsoleConfig, loop: ArmableLoop, session: string, armedAlready: boolean): { attempt: ArmAttempt; started: boolean } {
  const running = activeAttempt(config, loop);
  if (running !== null) return { attempt: running, started: false };
  const at = deps.now().toISOString();
  const a: ArmAttempt = {
    id: crypto.randomBytes(9).toString("base64url"),
    loop,
    session,
    startedAt: at,
    finishedAt: armedAlready ? at : null,
    state: armedAlready ? "done" : "running",
    step: armedAlready ? "done" : "writing",
    recordWritten: armedAlready,
    needsSupervisor: needsSupervisor(loop),
    noop: armedAlready,
    message: armedAlready ? `${loop} was already armed for ${session}; nothing was run.` : "Arming.",
    detail: [],
    armedToday: armedAlready ? true : null,
  };
  load(config).push(a);
  save(config);
  return { attempt: a, started: !armedAlready };
}

function failWith(config: ConsoleConfig, a: ArmAttempt, message: string, detail: string[], armedToday: boolean | null): void {
  update(config, a, { state: "failed", step: "done", finishedAt: deps.now().toISOString(), message, detail, armedToday });
}

/**
 * Run the arm for an attempt that `beginAttempt` opened. Never throws; the outcome is the attempt's
 * state. The one place the module's arm command is run from the console.
 */
export async function runAttempt(config: ConsoleConfig, a: ArmAttempt): Promise<void> {
  try {
    const out: CommandOutcome = await runArm(a.loop);
    if (!out.ok) {
      // The module's refusal is final and shown as it came back. A command that could not be run is
      // not a refusal: say what a fresh status finds, since nothing is known.
      if (out.failed) {
        const now = await readArmedToday(a.loop);
        failWith(
          config,
          a,
          out.error ?? "the arm command did not answer",
          [],
          now.ok ? now.armed : null,
        );
        return;
      }
      failWith(config, a, out.error ?? "the module refused", out.blockers, false);
      return;
    }
    update(config, a, {
      recordWritten: true,
      step: needsSupervisor(a.loop) ? "waiting" : "verifying",
      message: needsSupervisor(a.loop) ? "Arm record written. Waiting for the supervisor." : "Arm record written. Checking the loop.",
      detail: out.warning === null ? [] : [out.warning],
    });
    const job = JOB_OF[a.loop];
    const deadline = deps.now().getTime() + deps.waitMs;
    let last: Awaited<ReturnType<typeof readArmedToday>> = { ok: false, armed: false, session: a.session, error: null };
    for (;;) {
      last = await readArmedToday(a.loop);
      const ticking = job === null || jobTicking(readJob(config, job), deps.now().getTime());
      if (last.ok && last.armed && ticking) {
        update(config, a, {
          state: "done",
          step: "done",
          finishedAt: deps.now().toISOString(),
          armedToday: true,
          message:
            job === null
              ? `${a.loop} is armed for ${last.session}.`
              : `${a.loop} is armed for ${last.session}: the supervisor has enabled ${job} and its next run is ahead.`,
        });
        return;
      }
      if (deps.now().getTime() >= deadline) break;
      update(config, a, { step: job === null || (last.ok && last.armed) ? "verifying" : "waiting" });
      await deps.sleep(deps.pollMs);
    }
    // Out of time. The record was written, so say what the module itself says now.
    const armed = last.ok ? last.armed : null;
    failWith(
      config,
      a,
      armed === true
        ? `The arm record is written and ${a.loop} reads as ARMED for ${last.session}, but the supervisor has not enabled ${job ?? "its job"} with a next run in the last minute. Nothing is ticking yet. Check the supervisor on the System page, or switch ${a.loop} off.`
        : armed === false
          ? `The arm command answered ok, but ${a.loop} does not read as armed for ${last.session}. It is not armed.`
          : `The arm command answered ok, but the loop's status could not be read (${last.error ?? "no answer"}). Check ${a.loop} before relying on it.`,
      [],
      armed,
    );
  } catch (err) {
    failWith(config, a, `arming stopped on an error: ${(err as Error).message}`, [], null);
  }
}

/**
 * At start: an attempt left running by a console that went away is resolved by asking the module. The
 * arm command is not run again.
 */
export async function recoverAttempts(config: ConsoleConfig): Promise<void> {
  for (const a of load(config).filter((x) => x.state === "running")) {
    const now = await readArmedToday(a.loop);
    if (now.ok && now.armed) {
      update(config, a, { state: "done", step: "done", finishedAt: deps.now().toISOString(), recordWritten: true, armedToday: true, message: `${a.loop} reads as armed for ${now.session}; the console restarted while it was arming, so the supervisor check was not run.` });
    } else {
      failWith(config, a, `The console restarted while ${a.loop} was arming. ${now.ok ? `${a.loop} does not read as armed.` : "Its status could not be read."} Nothing was run again.`, [], now.ok ? now.armed : null);
    }
  }
}
