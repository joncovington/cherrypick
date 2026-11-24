import crypto from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { suitePython } from "@console/shared/paths";
import type { ArmableLoop, LiveArmFact, LiveArmReadout } from "@console/shared";
import { REPO_ROOT } from "../config.js";
import { maskAccount, redactAccounts } from "../readers/ladderLive.js";
import { etDate } from "../readers/ladder.js";
import { shapeStatus } from "./ladderLiveBridge.js";
import { shapeReadiness } from "./meicStatusBridge.js";
import { pythonArgv, spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Live page's arm switches reach the modules here, and only here.
 *
 * Arming is the module's own act: `python -m cherrypick.<module>.live_loop --arm --confirm YES` is the
 * command a person without Claude Code runs. It prints DISCLAIMER.md, refuses anything but the literal
 * word, refuses while its own checks fail, and writes today's arm record. This file holds NO arming
 * logic of its own: it asks the module whether it is ready (`--status`), shows the answer, and passes
 * the person's typed confirmation to the module's command. The agent path (`--install-task`) is never
 * used: it prompts for nothing, because a slash command stands behind it, and nothing here is one.
 *
 * What is NOT here, deliberately: any way to clear the halt flag (the Config page owns that, behind
 * its own typed phrase), any way to change a config, and any command that places or cancels an order.
 * The two argv builders below are the only verbs this file can run besides `--status`.
 */

export const ARMABLE: readonly ArmableLoop[] = ["meic", "ladder"];

const MODULE_OF: Record<ArmableLoop, string> = { meic: "cherrypick.meic.live_loop", ladder: "cherrypick.ladder.live_loop" };

export const isArmable = (loop: string): loop is ArmableLoop => (ARMABLE as readonly string[]).includes(loop);

/** The module's own command for the person's arm. The word is a literal, never taken from a request. */
export function armArgv(loop: ArmableLoop): string[] {
  return ["-m", MODULE_OF[loop], "--arm", "--confirm", "YES"];
}

export function disarmArgv(loop: ArmableLoop): string[] {
  return ["-m", MODULE_OF[loop], "--disarm"];
}

// ------------------------------------------------------------------------------ the subprocess

export interface RunResult {
  status: number | null;
  stdout: string;
  stderr: string;
  /** The process could not be started or was killed at its limit. */
  failure: string | null;
}

export type Runner = (argv: string[]) => Promise<RunResult>;

const RUN_TIMEOUT_MS = 120_000;

/**
 * Run `python <argv>` off the event loop. Never rejects. The real runner refuses to run under the test
 * runner, so a test that forgot to inject one cannot arm anything.
 */
const spawnRunner: Runner = (argv) =>
  new Promise((resolve) => {
    if (process.env["VITEST"] !== undefined) {
      resolve({ status: null, stdout: "", stderr: "", failure: "the real arm runner is refused under the test runner" });
      return;
    }
    try {
      execFile(
        suitePython(),
        pythonArgv(argv),
        { encoding: "utf-8", timeout: RUN_TIMEOUT_MS, windowsHide: true, maxBuffer: 16 * 1024 * 1024 },
        (err, stdout, stderr) => {
          if (err !== null && (err as { killed?: boolean }).killed === true) {
            resolve({ status: null, stdout, stderr, failure: `the command timed out after ${RUN_TIMEOUT_MS / 1000} s` });
            return;
          }
          const code = err === null ? 0 : typeof (err as { code?: unknown }).code === "number" ? (err as { code: number }).code : null;
          resolve({ status: code, stdout, stderr, failure: err !== null && code === null ? err.message : null });
        },
      );
    } catch (err) {
      resolve({ status: null, stdout: "", stderr: "", failure: (err as Error).message });
    }
  });

let runner: Runner = spawnRunner;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setArmRunner(fn?: Runner): void {
  runner = fn ?? spawnRunner;
}

/**
 * The JSON document a live_loop command prints last. `--arm` prints DISCLAIMER.md first and the answer
 * after it (`json.dumps(..., indent=2)`, so its opening brace stands alone on a line), and a refusal
 * exits 1 with the document still on stdout.
 */
export function parseCommandJson(stdout: string): Record<string, unknown> | null {
  const whole = (text: string): Record<string, unknown> | null => {
    try {
      const v = JSON.parse(text) as unknown;
      return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  };
  const direct = whole(stdout.trim());
  if (direct !== null) return direct;
  for (const m of stdout.matchAll(/^\{[ \t]*$/gm)) {
    const doc = whole(stdout.slice(m.index).trim());
    if (doc !== null) return doc;
  }
  return null;
}

/** What the module's command said, as the person is shown it. */
export interface CommandOutcome {
  /** The module said `ok: true`. */
  ok: boolean;
  /** The command could not be run or its answer could not be read: not a refusal, an unknown. */
  failed: boolean;
  doc: Record<string, unknown> | null;
  error: string | null;
  blockers: string[];
  warning: string | null;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export function shapeOutcome(res: RunResult): CommandOutcome {
  if (res.failure !== null) {
    return { ok: false, failed: true, doc: null, error: redactAccounts(`the command could not be run: ${res.failure}`), blockers: [], warning: null };
  }
  const doc = parseCommandJson(res.stdout);
  if (doc === null) {
    const tail = res.stderr.trim().split(/\r?\n/).pop() ?? "";
    return { ok: false, failed: true, doc: null, error: redactAccounts(`no readable answer from the command (exit ${String(res.status)})${tail ? `: ${tail}` : ""}`), blockers: [], warning: null };
  }
  const ok = doc["ok"] === true && res.status === 0;
  const unmet = strings(doc["unmet_gates"]);
  return {
    ok,
    failed: false,
    doc,
    error: ok ? null : redactAccounts(typeof doc["error"] === "string" ? doc["error"] : "the command refused"),
    // Said as they came back: MEIC's `blockers`, the ladder's `unmet_gates`.
    blockers: [...strings(doc["blockers"]), ...unmet].map(redactAccounts),
    warning: typeof doc["warning"] === "string" ? redactAccounts(doc["warning"]) : null,
  };
}

export async function runArm(loop: ArmableLoop): Promise<CommandOutcome> {
  return shapeOutcome(await runner(armArgv(loop)));
}

export async function runDisarm(loop: ArmableLoop): Promise<CommandOutcome> {
  return shapeOutcome(await runner(disarmArgv(loop)));
}

// ------------------------------------------------------------------------------ the module's status

type StatusCaller = (loop: ArmableLoop) => SpawnedJson | Promise<SpawnedJson>;

const UNAVAILABLE: Record<ArmableLoop, string> = {
  meic: "MEIC status unavailable — the meic package must be installed (pip install -e packages/meic)",
  ladder: "ladder live status unavailable — is cherrypick-ladder installed?",
};

const spawnStatus: StatusCaller = (loop) => spawnModuleCliAsync(["-m", MODULE_OF[loop], "--status"], UNAVAILABLE[loop]);

let statusCaller: StatusCaller = spawnStatus;

/** Swap the status read out in tests. Pass nothing to restore the real one. */
export function setArmStatusCaller(fn?: StatusCaller): void {
  statusCaller = fn ?? spawnStatus;
}

export type AccountProbe = () => SpawnedJson | Promise<SpawnedJson>;

// MEIC's `--status` names no account, and the arming command says to. This reads the designated
// account's number from the keyring the module reads, and prints only the masked form. No broker call.
const ACCOUNT_CODE =
  "import json;from cherrypick.meic import credentials as c;from cherrypick.core.redact import mask_account as m;" +
  "a=c.store.designated_account();print(json.dumps({'account':m(a) if a else None}))";

const spawnAccount: AccountProbe = () => spawnModuleCliAsync(["-c", ACCOUNT_CODE], "the designated account is not readable here");

let accountProbe: AccountProbe = spawnAccount;

export function setAccountProbe(fn?: AccountProbe): void {
  accountProbe = fn ?? spawnAccount;
}

// ------------------------------------------------------------------------------ the readout

const usd = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? `$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}` : "not set");
const text = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** DISCLAIMER.md at the repo root, byte for byte; null when the file is missing. */
export function readDisclaimer(root: string = REPO_ROOT): string | null {
  try {
    return fs.readFileSync(path.join(root, "DISCLAIMER.md"), "utf-8");
  } catch {
    return null;
  }
}

const sha = (s: string): string => crypto.createHash("sha256").update(s).digest("hex");

export interface Readout {
  readout: LiveArmReadout;
  /** What the readout was made of, hashed: the confirm must still find the module saying the same. */
  hash: string;
}

function blank(loop: ArmableLoop, error: string): Readout {
  return {
    hash: "",
    readout: {
      ok: false,
      error: redactAccounts(error),
      loop,
      title: loop,
      session: null,
      ready: false,
      blockers: [],
      warnings: [],
      halted: false,
      armedToday: false,
      supervisorFresh: null,
      facts: [],
      mode: null,
      modeNote: null,
      disclaimer: null,
      dialogId: null,
    },
  };
}

async function meicReadout(res: SpawnedJson, disclaimer: string | null): Promise<Readout> {
  const verdict = shapeReadiness(res);
  if (!verdict.ok || res.json === null) return blank("meic", verdict.ok ? "no answer" : verdict.error);
  const d = res.json;
  const session = text(d["date"]) ?? etDate();
  const probe = await accountProbe();
  const account = probe.ok && probe.json !== null ? maskAccount(probe.json["account"]) : null;
  const symbol = text(d["symbol"]);
  const armName = text(d["arm"]);
  const window = Array.isArray(d["entry_window"]) ? (d["entry_window"] as unknown[]).map((x) => text(x)) : [];
  const perSide = d["per_side_stop_management"] === true;
  const cap = d["max_structures_per_day"];
  const open = num(d["open_positions"]);
  const pending = Array.isArray(d["pending_orders"]) ? d["pending_orders"].length : 0;
  const halted = d["halt_flag"] === true;
  const supervisor = d["supervisor_fresh"] === true;
  const loss = num(d["daily_loss_halt_dollars"]);
  const facts: LiveArmFact[] = [
    { label: "account", value: account ?? "designated account not readable here" },
    { label: "symbol · arm", value: `${symbol ?? "no symbol pinned"} · ${armName ?? "the top-level settings (no live.arm)"}` },
    {
      label: "new entries",
      value:
        d["dry_run"] === false
          ? "LIVE: real condor orders are sent"
          : d["dry_run"] === true
            ? "DRY RUN (live.dry_run): each entry is preflighted against the account and none is sent; a held book is still managed"
            : "not reported by this build of the loop",
    },
    { label: "entry window", value: window[0] && window[1] ? `${window[0]} to ${window[1]} ET` : "not set" },
    { label: "max new condors a day", value: cap === null ? "no count cap (sized by buying power alone)" : cap === undefined ? "1" : String(cap) },
    { label: "open buying power cap", value: usd(d["max_open_bp_dollars"]) },
    {
      label: "per-side stops",
      value: perSide ? `on · at ${String(d["stop_trigger_ratio"] ?? "?")} × credit` : "off · every condor is held to settlement; the worst case is its width less its credit",
    },
    { label: "self-disarms", value: `${text(d["disarm_time"]) ?? "17:00"} ET` },
    { label: "daily loss breaker", value: loss === null ? "NOT SET: with stops off it is the only intraday brake besides the halt flag" : `stops entries at −${usd(loss)} gross` },
    { label: "today so far", value: `${open === null ? "?" : String(open)} open · ${String(pending)} working · ${String(num(d["entries_today"]) ?? 0)} entries${d["session_settled"] === true ? " · session settled" : ""}` },
    { label: "supervisor", value: supervisor ? "running" : "NOT running: nothing would tick" },
    { label: "halt flag", value: halted ? "SET" : "clear" },
  ];
  const warnings = [...verdict.warnings];
  if (d["breaker_tripped"] === true) facts.push({ label: "breaker", value: "TRIPPED today: no new condor" });
  return finish("meic", {
    session,
    ready: verdict.ready,
    blockers: verdict.blockers,
    warnings,
    halted,
    armedToday: text(d["armed_for"]) === session,
    supervisorFresh: supervisor,
    facts,
    mode: null,
    modeNote: null,
    title: `meic · ${symbol ?? "no symbol"}`,
    disclaimer,
  });
}

function ladderReadout(res: SpawnedJson, disclaimer: string | null): Readout {
  const s = shapeStatus(res);
  if (!s.ok) return blank("ladder", s.error ?? "no answer");
  // The module's own `ready` counts the arm record as a gate, which cannot hold before arming: what
  // blocks an arm is any OTHER gate that stops the loop on the account, as the module words it.
  const blockers = s.gates.filter((g) => !g.ok && g.blocks === "all" && g.name !== "arm.record").map((g) => `${g.name}: ${g.reason}`);
  const warnings = s.gates.filter((g) => !g.ok && g.blocks === "new_buys").map((g) => `${g.name}: ${g.reason}`);
  if (s.held.unresolved.length > 0 || s.held.unrecorded > 0) warnings.push("a submission is held: an uncertain order is still unresolved");
  const live = s.mode === "live";
  const facts: LiveArmFact[] = [
    { label: "account", value: s.account ?? "no designated account" },
    { label: "mode", value: live ? "LIVE: places and cancels real orders" : "dry-run: reads the broker and journals what it would send; places and cancels nothing" },
    ...s.gates.map((g) => ({ label: g.name, value: `${g.ok ? "ok" : "FAILING"} · ${g.reason}` })),
    { label: "halt flag", value: s.haltFlag === true ? "SET" : "clear" },
  ];
  return finish("ladder", {
    session: s.today ?? etDate(),
    ready: blockers.length === 0,
    blockers,
    warnings,
    halted: s.haltFlag === true,
    armedToday: s.armedToday === true,
    supervisorFresh: null,
    facts,
    mode: s.mode,
    modeNote: s.note,
    title: "ladder",
    disclaimer,
  });
}

type Core = Omit<LiveArmReadout, "ok" | "error" | "loop" | "dialogId">;

function finish(loop: ArmableLoop, core: Core): Readout {
  // The hash is what the person was shown, less anything that only says when it was read.
  const hash = sha(
    JSON.stringify({
      loop,
      session: core.session,
      ready: core.ready,
      blockers: core.blockers,
      warnings: core.warnings,
      halted: core.halted,
      armedToday: core.armedToday,
      facts: core.facts,
      mode: core.mode,
      disclaimer: core.disclaimer === null ? null : sha(core.disclaimer),
    }),
  );
  return { hash, readout: { ok: true, error: null, loop, ...core, blockers: core.blockers.map(redactAccounts), warnings: core.warnings.map(redactAccounts), dialogId: null } };
}

/**
 * The module's readiness, read fresh (never the memoised readouts the cards use: a check that the
 * answer is unchanged must not compare a reading to itself), with the disclaimer and a hash of both.
 */
export async function buildReadout(loop: ArmableLoop, root: string = REPO_ROOT): Promise<Readout> {
  const res = await statusCaller(loop);
  const disclaimer = readDisclaimer(root);
  return loop === "meic" ? meicReadout(res, disclaimer) : ladderReadout(res, disclaimer);
}

/** A fresh `--status` reduced to what an attempt verifies: armed today, and the module's date. */
export async function readArmedToday(loop: ArmableLoop): Promise<{ ok: boolean; armed: boolean; session: string; error: string | null }> {
  const res = await statusCaller(loop);
  if (!res.ok || res.json === null) return { ok: false, armed: false, session: etDate(), error: res.error ?? "no answer" };
  const d = res.json;
  if (loop === "meic") {
    const session = text(d["date"]) ?? etDate();
    return { ok: true, armed: text(d["armed_for"]) === session, session, error: null };
  }
  const session = text(d["today"]) ?? etDate();
  return { ok: true, armed: d["armed_today"] === true, session, error: null };
}

// ------------------------------------------------------------------------------ the one-use dialog token

const DIALOG_TTL_MS = 10 * 60_000;
const MAX_DIALOGS = 32;
interface Dialog {
  loop: ArmableLoop;
  hash: string;
  session: string;
  expires: number;
}
const dialogs = new Map<string, Dialog>();

/** A token for exactly this readout. Issued only for a loop that is ready, unhalted and not yet armed. */
export function issueDialog(loop: ArmableLoop, r: Readout, now = Date.now()): string | null {
  const o = r.readout;
  if (!o.ok || !o.ready || o.halted || o.armedToday || o.disclaimer === null || o.session === null) return null;
  for (const [id, d] of dialogs) if (d.expires <= now) dialogs.delete(id);
  while (dialogs.size >= MAX_DIALOGS) dialogs.delete(dialogs.keys().next().value as string);
  const id = crypto.randomBytes(18).toString("base64url");
  dialogs.set(id, { loop, hash: r.hash, session: o.session, expires: now + DIALOG_TTL_MS });
  return id;
}

/** Spend a token. It is gone whatever happens next: a confirmation is never reused. */
export function spendDialog(id: unknown, loop: ArmableLoop, now = Date.now()): Dialog | null {
  if (typeof id !== "string") return null;
  const d = dialogs.get(id);
  if (d === undefined) return null;
  dialogs.delete(id);
  return d.loop === loop && d.expires > now ? d : null;
}

/** Drop every token. Tests only. */
export function resetDialogs(): void {
  dialogs.clear();
}
