import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify, { type FastifyInstance } from "fastify";
import type { ArmAttempt, LiveArmReadout } from "@console/shared";
import { REPO_ROOT, type ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { registerLiveArmingRoutes } from "../src/routes/liveArming.js";
import { setBridgeCaller, type BridgeRequest } from "../src/services/configBridge.js";
import { beginAttempt, listAttempts, recoverAttempts, resetAttempts, setAttemptDeps } from "../src/services/liveArmAttempts.js";
import {
  armArgv,
  buildReadout,
  disarmArgv,
  parseCommandJson,
  resetDialogs,
  runArm,
  setAccountProbe,
  setArmRunner,
  setArmStatusCaller,
  type RunResult,
} from "../src/services/liveArmingBridge.js";
import { resetMeicStatusCache, setMeicStatusCaller } from "../src/services/meicStatusBridge.js";

/**
 * The Live page's arm switches. What this file pins is every way the console could put a live loop on
 * by accident: a flag that is off, a loop that is retired, a confirmation that is not the literal word
 * or not from a readout the server served, a halt flag, a loop that is not ready, a readout that moved
 * after it was read, and a module that refuses. No test here runs a real arm command: the runner is
 * injected, and at rest it is a stub that fails the test if it is called.
 */

let tmp: string;
let config: ConsoleConfig;
let app: FastifyInstance;
let runs: string[][] = [];
let halfDone: Array<() => void> = [];

const TODAY = "2026-11-24";
const HALT = () => path.join(tmp, "state", "halt-live.flag");

/** MEIC's `--status` as the module prints it, one knob at a time. */
let meic: Record<string, unknown>;
let ladder: Record<string, unknown>;

function meicStatus(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    date: TODAY,
    armed_for: null,
    disarm_time: "17:00",
    unmet_gates: [],
    symbol: "SPX",
    arm: "pm-200",
    entry_window: ["09:45", "15:00"],
    max_open_bp_dollars: 4000,
    max_structures_per_day: 1,
    entries_today: 0,
    per_side_stop_management: true,
    stop_trigger_ratio: 2.0,
    open_positions: 0,
    pending_orders: [],
    open_ics: [],
    criticals_today: [{ at: "2026-11-24T10:00:00-05:00", message: "noise that must not move the hash" }],
    session_settled: true,
    halt_flag: fs.existsSync(HALT()),
    breaker_tripped: false,
    supervisor_fresh: true,
    daily_loss_halt_dollars: 200,
    arm_ready: { ready: true, blockers: [], warnings: [] },
    ...over,
  };
}

function ladderStatus(over: Record<string, unknown> = {}): Record<string, unknown> {
  const gate = (name: string, ok = true, reason = "ok", blocks = "all") => ({ name, ok, reason, blocks });
  return {
    ok: true,
    dry_run: true,
    mode: "dry-run",
    note: "dry-run",
    held: { unresolved: [], unrecorded: {} },
    today: TODAY,
    armed_today: false,
    armed_for: null,
    account: "9999991234",
    halt_flag: fs.existsSync(HALT()),
    ready: false,
    new_buys_allowed: false,
    gates: [
      gate("live.enabled"),
      gate("arm.record", false, "not armed"),
      gate("account.designated"),
      gate("halt.flag", !fs.existsSync(HALT()), "halt", "new_buys"),
    ],
    ...over,
  };
}

const outcome = (doc: Record<string, unknown>, status = 0, pre = ""): RunResult => ({
  status,
  stdout: `${pre}${JSON.stringify(doc, null, 2)}\n`,
  stderr: "",
  failure: null,
});

/** The runner a test installs: it records every argv, and applies `effect` as the module's command would. */
function installRunner(effect: (argv: string[]) => RunResult | Promise<RunResult>) {
  setArmRunner(async (argv) => {
    runs.push(argv);
    return effect(argv);
  });
}

const writeJobs = (next: string | null, enabled = true) => {
  fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "state", "supervisor-jobs.json"), JSON.stringify({ jobs: { "meic-live": { enabled, next_run: next } } }));
};
const future = () => new Date(Date.now() + 60_000).toISOString();

const setFlag = (v: unknown) => {
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config", "console.json"), JSON.stringify({ live_arming: v }));
};

const headers = { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN };
const post = (url: string, payload: unknown) => app.inject({ method: "POST", url, payload: payload as object, headers });
const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

async function readout(loop = "meic"): Promise<LiveArmReadout> {
  const res = await get(`/api/live/arming/${loop}/readout`);
  expect(res.statusCode).toBe(200);
  return res.json() as LiveArmReadout;
}

/** Wait for an attempt to leave `running`. The fake clock makes this quick. */
async function settled(id: string): Promise<ArmAttempt> {
  for (let i = 0; i < 200; i++) {
    const a = listAttempts(config).find((x) => x.id === id);
    if (a !== undefined && a.state !== "running") return a;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("the attempt never finished");
}

let clock = 0;
let setHaltCalls: BridgeRequest[] = [];

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-arming-test-"));
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
      reviewDir: path.join(tmp, "review"),
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  } as ConsoleConfig;
  app = Fastify();
  registerSecurity(app);
  registerLiveArmingRoutes(app, config);
  await app.ready();
});

beforeEach(() => {
  runs = [];
  halfDone = [];
  setHaltCalls = [];
  clock = Date.now();
  fs.rmSync(HALT(), { force: true });
  fs.rmSync(path.join(tmp, "console"), { recursive: true, force: true });
  fs.rmSync(path.join(tmp, "state", "supervisor-jobs.json"), { force: true });
  resetAttempts();
  resetDialogs();
  resetMeicStatusCache();
  setFlag(true);
  meic = {};
  ladder = {};
  // At rest the runner is a stub that fails the test: nothing here may start a real command.
  setArmRunner(() => {
    throw new Error("the arm runner was called without the test installing one");
  });
  setArmStatusCaller((loop) => ({ ok: true, json: loop === "meic" ? meicStatus(meic) : ladderStatus(ladder), error: null }));
  setAccountProbe(() => ({ ok: true, json: { account: "****1234" }, error: null }));
  // Any attempt to touch the halt flag through the config editor is recorded here.
  setBridgeCaller((req) => {
    setHaltCalls.push(req);
    return { ok: true };
  });
  setAttemptDeps({
    now: () => new Date(clock),
    sleep: async (ms) => {
      clock += ms;
    },
    waitMs: 9_000,
    pollMs: 3_000,
  });
});

afterEach(() => {
  setArmRunner();
  setArmStatusCaller();
  setAccountProbe();
  setMeicStatusCaller();
  setBridgeCaller();
  setAttemptDeps();
  for (const f of halfDone) f();
});

describe("the flag: off ships, and off means every route is shut", () => {
  it.each([[false], ["true"], [1], [null], ["yes"]])("is off for live_arming %j (only a literal true is on)", async (v) => {
    setFlag(v);
    expect((await get("/api/live/arming")).json()).toEqual({ enabled: false, switches: [], attempts: [] });
    expect((await get("/api/live/arming/meic/readout")).statusCode).toBe(403);
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: "x" })).statusCode).toBe(403);
    expect((await post("/api/live/arming/meic/disarm", { disarm: true })).statusCode).toBe(403);
    expect(runs).toEqual([]);
  });

  it("is off with no console.json at all", async () => {
    fs.rmSync(path.join(tmp, "config", "console.json"));
    expect((await get("/api/live/arming")).json()).toMatchObject({ enabled: false });
    expect((await get("/api/live/arming/ladder/readout")).statusCode).toBe(403);
  });

  it("is on for a literal true, with a working switch for meic and the ladder", async () => {
    const s = (await get("/api/live/arming")).json() as { enabled: boolean; switches: Array<{ loop: string; state: string }> };
    expect(s.enabled).toBe(true);
    expect(s.switches.map((x) => `${x.loop}:${x.state}`)).toEqual(["meic:armable", "ladder:armable"]);
  });
});

describe("the writes meet the mutating-surface posture", () => {
  it("refuses a missing csrf token, a foreign origin and a non-JSON body", async () => {
    const base = { method: "POST" as const, url: "/api/live/arming/meic/arm", payload: { confirm: "YES" } };
    expect((await app.inject({ ...base, headers: { host: "127.0.0.1:5070" } })).statusCode).toBe(403);
    expect((await app.inject({ ...base, headers: { ...headers, origin: "https://evil.example" } })).statusCode).toBe(403);
    expect((await app.inject({ ...base, headers: { ...headers, "content-type": "text/plain" }, payload: "YES" })).statusCode).toBe(415);
    expect((await app.inject({ ...base, headers: { host: "evil.example", "x-csrf-token": CSRF_TOKEN } })).statusCode).toBe(403);
    expect(runs).toEqual([]);
  });
});

describe("retired and unknown loops have no switch and refuse", () => {
  it.each(["flies", "bwb", "meic.margin", "weeklyput", "..%2Fmeic"])("%s", async (loop) => {
    installRunner(() => outcome({ ok: true }));
    expect((await get(`/api/live/arming/${loop}/readout`)).statusCode).toBe(404);
    expect((await post(`/api/live/arming/${loop}/arm`, { confirm: "YES", dialogId: "x" })).statusCode).toBe(403);
    expect((await post(`/api/live/arming/${loop}/disarm`, { disarm: true })).statusCode).toBe(403);
    expect(runs).toEqual([]);
    expect(((await get("/api/live/arming")).json() as { switches: Array<{ loop: string }> }).switches.map((x) => x.loop)).not.toContain(loop);
  });

  it("shows a second login's account as a locked switch, with its blocker", async () => {
    fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
    fs.writeFileSync(path.join(tmp, "config", "meic.json"), JSON.stringify({ live_accounts: { margin: { enabled: true } } }));
    fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({ broker: { logins: [{ name: "margin" }] } }));
    setMeicStatusCaller(() => ({ ok: true, json: meicStatus({ arm_ready: { ready: false, blockers: ["live gate unmet: kill criteria"], warnings: [] } }), error: null }));
    setArmStatusCaller((loop) => ({
      ok: true,
      json: loop === "meic" ? meicStatus({ arm_ready: { ready: false, blockers: ["live gate unmet: kill criteria"], warnings: [] } }) : ladderStatus(),
      error: null,
    }));
    const s = (await get("/api/live/arming")).json() as { switches: Array<{ loop: string; state: string; lockedReason: string }> };
    const margin = s.switches.find((x) => x.loop === "meic.margin");
    expect(margin).toMatchObject({ state: "locked", lockedReason: "live gate unmet: kill criteria" });
    expect((await post("/api/live/arming/meic.margin/arm", { confirm: "YES", dialogId: "x" })).statusCode).toBe(403);
    fs.rmSync(path.join(tmp, "config", "meic.json"));
    fs.rmSync(path.join(tmp, "config.json"));
  });
});

describe("the readout is the module's own, with the disclaimer verbatim", () => {
  it("states the facts the arming command says to state, and serves DISCLAIMER.md byte for byte", async () => {
    const r = await readout("meic");
    expect(r.disclaimer).toBe(fs.readFileSync(path.join(REPO_ROOT, "DISCLAIMER.md"), "utf-8"));
    expect(r.ready).toBe(true);
    expect(r.dialogId).not.toBeNull();
    const f = Object.fromEntries(r.facts.map((x) => [x.label, x.value]));
    expect(f["account"]).toBe("****1234");
    expect(f["symbol · arm"]).toBe("SPX · pm-200");
    expect(f["entry window"]).toBe("09:45 to 15:00 ET");
    expect(f["max new condors a day"]).toBe("1");
    expect(f["open buying power cap"]).toBe("$4,000");
    expect(f["per-side stops"]).toBe("on · at 2 × credit");
    expect(f["self-disarms"]).toBe("17:00 ET");
    expect(f["daily loss breaker"]).toBe("stops entries at −$200 gross");
    expect(f["today so far"]).toContain("0 open · 0 working · 0 entries");
    expect(f["supervisor"]).toBe("running");
    expect(f["halt flag"]).toBe("clear");
  });

  it("says whether new entries are a dry run, from the status and never by assumption", async () => {
    const entries = async (): Promise<string> =>
      (await readout("meic")).facts.find((x) => x.label === "new entries")!.value;
    meic = { dry_run: true };
    expect(await entries()).toMatch(/^DRY RUN .*none is sent/);
    meic = { dry_run: false };
    expect(await entries()).toMatch(/^LIVE: real condor orders are sent/);
    meic = {};
    expect(await entries()).toMatch(/^not reported/);
  });

  it("says plainly when stops are off and when no breaker is set", async () => {
    meic = { per_side_stop_management: false, daily_loss_halt_dollars: null, max_structures_per_day: null };
    const f = Object.fromEntries((await readout("meic")).facts.map((x) => [x.label, x.value]));
    expect(f["per-side stops"]).toMatch(/^off · every condor is held to settlement/);
    expect(f["daily loss breaker"]).toMatch(/^NOT SET/);
    expect(f["max new condors a day"]).toMatch(/^no count cap/);
  });

  it("never carries an account number unmasked, even if the module's text does", async () => {
    meic = { arm_ready: { ready: false, blockers: ["orphaned orders at the broker: account=5WX01234567"], warnings: [] } };
    const r = await readout("meic");
    expect(JSON.stringify(r)).not.toContain("5WX01234567");
  });

  it("says whether the ladder is dry-run, and arms on its gates other than its own arm record", async () => {
    const dry = await readout("ladder");
    expect(dry.mode).toBe("dry-run");
    expect(dry.ready).toBe(true);
    expect(dry.dialogId).not.toBeNull();
    expect(dry.facts.find((x) => x.label === "mode")!.value).toMatch(/^dry-run: .*places and cancels nothing/);
    ladder = { dry_run: false, mode: "live" };
    const live = await readout("ladder");
    expect(live.mode).toBe("live");
    expect(live.facts.find((x) => x.label === "mode")!.value).toMatch(/^LIVE: places and cancels real orders/);
  });

  it("a ladder gate that stops the loop on the account is a blocker, and gets no token", async () => {
    ladder = { gates: [{ name: "live.enabled", ok: false, reason: "live.enabled is false", blocks: "all" }, { name: "arm.record", ok: false, reason: "not armed", blocks: "all" }] };
    const r = await readout("ladder");
    expect(r.ready).toBe(false);
    expect(r.blockers).toEqual(["live.enabled: live.enabled is false"]);
    expect(r.dialogId).toBeNull();
  });

  it("a status that cannot be read is an error, never a ready loop", async () => {
    setArmStatusCaller(() => ({ ok: false, json: null, error: "MEIC status unavailable" }));
    const r = await readout("meic");
    expect(r).toMatchObject({ ok: false, ready: false, dialogId: null });
  });
});

describe("guard: only the literal YES, typed by the browser", () => {
  it.each([["yes"], ["YES "], [" YES"], ["Yes"], ["Y"], ["YES\n"], [true], [1], [null], [undefined], [["YES"]]])("refuses confirm %j and runs nothing", async (c) => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: c, dialogId: r.dialogId });
    expect(res.statusCode).toBe(400);
    expect((res.json() as { code: string }).code).toBe("confirm_required");
    expect(runs).toEqual([]);
  });

  it("does not take the word from the query string or a header", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    const res = await app.inject({
      method: "POST",
      url: `/api/live/arming/meic/arm?confirm=YES&dialogId=${r.dialogId ?? ""}`,
      payload: {},
      headers: { ...headers, "x-confirm": "YES" },
    });
    expect(res.statusCode).toBe(400);
    expect(runs).toEqual([]);
  });

  it("refuses a YES with no token, an invented token, a token for the other loop and a token used before", async () => {
    installRunner(() => outcome({ ok: true, armed_for: TODAY }));
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES" })).statusCode).toBe(409);
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: "not-served-by-this-server" })).statusCode).toBe(409);
    const ladderDialog = (await readout("ladder")).dialogId;
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: ladderDialog })).statusCode).toBe(409);
    expect(runs).toEqual([]);
    // One real confirmation, then the same one again: the second is refused whatever the state.
    const r = await readout("meic");
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId })).statusCode).toBe(202);
    const again = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(again.statusCode).toBe(409);
    expect((again.json() as { code: string }).code).toBe("readout_expired");
  });

  it("refuses a token that has aged out", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    const real = Date.now;
    Date.now = () => real() + 11 * 60_000;
    try {
      expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId })).statusCode).toBe(409);
    } finally {
      Date.now = real;
    }
    expect(runs).toEqual([]);
  });
});

describe("guard: refused while halted, and arming never clears the halt", () => {
  it("serves no token while the halt flag is up, and the arm is refused", async () => {
    installRunner(() => outcome({ ok: true }));
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    fs.writeFileSync(HALT(), "");
    const r = await readout("meic");
    expect(r.halted).toBe(true);
    expect(r.dialogId).toBeNull();
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(409);
    expect(runs).toEqual([]);
    expect(fs.existsSync(HALT())).toBe(true);
  });

  it("refuses when the halt goes up after the dialog opened", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    expect(r.dialogId).not.toBeNull();
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    fs.writeFileSync(HALT(), "");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(409);
    expect((res.json() as { code: string }).code).toBe("halted");
    expect(runs).toEqual([]);
    expect(fs.existsSync(HALT())).toBe(true);
  });

  it("the ladder too: the module would arm it under a halt, the console does not", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("ladder");
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    fs.writeFileSync(HALT(), "");
    const res = await post("/api/live/arming/ladder/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(409);
    expect(runs).toEqual([]);
  });

  it("never touches the halt flag through a whole arm, even one that finishes while the halt is raised", async () => {
    installRunner(() => {
      // The module's command runs, and the halt goes up as it finishes.
      fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
      fs.writeFileSync(HALT(), "");
      meic = { armed_for: TODAY };
      return outcome({ ok: true, armed_for: TODAY });
    });
    writeJobs(future());
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    const done = await settled((res.json() as { attempt: ArmAttempt }).attempt.id);
    expect(done.state).toBe("done");
    expect(fs.existsSync(HALT())).toBe(true);
    expect(setHaltCalls.filter((c) => c.op === "set_halt")).toEqual([]);
    expect(setHaltCalls).toEqual([]);
  });

  it("an armed loop can still be switched off while halted", async () => {
    installRunner((argv) => (argv.includes("--disarm") ? outcome({ ok: true, arm_record_removed: true, detail: "disarmed (arm record removed)" }) : outcome({ ok: false })));
    fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
    fs.writeFileSync(HALT(), "");
    const res = await post("/api/live/arming/meic/disarm", { disarm: true });
    expect(res.statusCode).toBe(200);
    expect(runs).toEqual([disarmArgv("meic")]);
    expect(fs.existsSync(HALT())).toBe(true);
  });
});

describe("guard: a loop that is not ready gets no arm call", () => {
  it("serves only the blockers and no token", async () => {
    meic = { arm_ready: { ready: false, blockers: ["the supervisor is not running: nothing would tick"], warnings: [] } };
    const r = await readout("meic");
    expect(r).toMatchObject({ ready: false, blockers: ["the supervisor is not running: nothing would tick"], dialogId: null });
  });

  it("refuses a confirm when readiness is lost after the dialog opened", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    meic = { arm_ready: { ready: false, blockers: ["orphaned orders at the broker: ['x']"], warnings: [] } };
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(409);
    expect(res.json()).toMatchObject({ code: "not_ready", blockers: ["orphaned orders at the broker: ['x']"] });
    expect(runs).toEqual([]);
  });

  it("a verdict that says ready while listing a blocker is not ready", async () => {
    meic = { arm_ready: { ready: true, blockers: ["x"], warnings: [] } };
    expect((await readout("meic")).dialogId).toBeNull();
  });

  it("a status that cannot be read at confirm time runs nothing", async () => {
    installRunner(() => outcome({ ok: true }));
    const r = await readout("meic");
    setArmStatusCaller(() => ({ ok: false, json: null, error: "boom" }));
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(502);
    expect(runs).toEqual([]);
  });
});

describe("guard: bound to what was read", () => {
  it("refuses with 409 and a fresh readout when a condor opened since the dialog", async () => {
    installRunner(() => outcome({ ok: true, armed_for: TODAY }));
    const r = await readout("meic");
    meic = { open_positions: 1, open_ics: ["LIVE-SPX-1"] };
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(409);
    const body = res.json() as { code: string; readout: LiveArmReadout };
    expect(body.code).toBe("readout_changed");
    expect(body.readout.facts.find((x) => x.label === "today so far")!.value).toContain("1 open");
    expect(runs).toEqual([]);
    // The fresh readout carries its own token, and that one works.
    expect(body.readout.dialogId).not.toBeNull();
    expect((await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: body.readout.dialogId })).statusCode).toBe(202);
  });

  it("refuses when a warning appeared, and ignores what only says when it was read", async () => {
    installRunner(() => outcome({ ok: true, armed_for: TODAY }));
    writeJobs(future());
    const r = await readout("meic");
    // Noise: a new critical's timestamp changes nothing the person read.
    meic = { criticals_today: [{ at: "2026-11-24T11:00:00-05:00", message: "another" }] };
    const quiet = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(quiet.statusCode).toBe(202);
    resetDialogs();
    const r2 = await readout("meic");
    meic = { arm_ready: { ready: true, blockers: [], warnings: ["a daily-loss breaker has tripped"] } };
    const loud = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r2.dialogId });
    expect(loud.statusCode).toBe(409);
  });

  it("a different disclaimer is a different readout", async () => {
    const roots = [1, 2].map((n) => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "arming-disclaimer-"));
      fs.writeFileSync(path.join(dir, "DISCLAIMER.md"), `text ${String(n)}`);
      return dir;
    });
    const [a, b] = await Promise.all(roots.map((r) => buildReadout("meic", r)));
    expect(a!.hash).not.toBe(b!.hash);
    expect(a!.readout.disclaimer).toBe("text 1");
    for (const r of roots) fs.rmSync(r, { recursive: true, force: true });
  });
});

describe("an arm that is confirmed", () => {
  it("runs the module's own person-path command and nothing else, and answers 202 at once", async () => {
    installRunner(() => {
      meic = { armed_for: TODAY };
      return outcome({ ok: true, driver: "supervisor", armed_for: TODAY, detail: "arm record written" }, 0, "DISCLAIMER TEXT FIRST\n{not json}\n");
    });
    writeJobs(future());
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    expect(res.statusCode).toBe(202);
    const a = (res.json() as { attempt: ArmAttempt }).attempt;
    expect(a.loop).toBe("meic");
    const done = await settled(a.id);
    expect(done).toMatchObject({ state: "done", recordWritten: true, armedToday: true });
    expect(done.message).toContain("meic-live");
    expect(runs).toEqual([["-m", "cherrypick.meic.live_loop", "--arm", "--confirm", "YES"]]);
    expect(runs[0]).not.toContain("--install-task");
    // Persisted for a page that comes back, or a console that restarts.
    expect(JSON.parse(fs.readFileSync(path.join(tmp, "console", "live-arming.json"), "utf-8")).attempts[0].id).toBe(a.id);
  });

  it("arms the ladder with its own module and needs no supervisor job", async () => {
    installRunner(() => {
      ladder = { armed_today: true, armed_for: TODAY };
      return outcome({ ok: true, armed_for: TODAY, dry_run: true });
    });
    const r = await readout("ladder");
    const res = await post("/api/live/arming/ladder/arm", { confirm: "YES", dialogId: r.dialogId });
    const done = await settled((res.json() as { attempt: ArmAttempt }).attempt.id);
    expect(done).toMatchObject({ state: "done", needsSupervisor: false });
    expect(runs).toEqual([["-m", "cherrypick.ladder.live_loop", "--arm", "--confirm", "YES"]]);
  });

  it("a module refusal is final and shown as it came back", async () => {
    installRunner(() => outcome({ ok: false, error: "not ready to arm", blockers: ["the supervisor is not running: nothing would tick"] }, 1));
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    const done = await settled((res.json() as { attempt: ArmAttempt }).attempt.id);
    expect(done).toMatchObject({ state: "failed", recordWritten: false, message: "not ready to arm", detail: ["the supervisor is not running: nothing would tick"], armedToday: false });
    expect(runs).toHaveLength(1);
  });

  it("a command that cannot be run says what a fresh status finds, not an assumption", async () => {
    installRunner(() => ({ status: null, stdout: "", stderr: "", failure: "spawn python ENOENT" }));
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    const done = await settled((res.json() as { attempt: ArmAttempt }).attempt.id);
    expect(done.state).toBe("failed");
    expect(done.message).toContain("could not be run");
    expect(done.armedToday).toBe(false);
  });

  it("when the supervisor never enables the job, says the loop IS armed and nothing is ticking", async () => {
    installRunner(() => {
      meic = { armed_for: TODAY };
      return outcome({ ok: true, armed_for: TODAY });
    });
    writeJobs(null);
    const r = await readout("meic");
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: r.dialogId });
    const done = await settled((res.json() as { attempt: ArmAttempt }).attempt.id);
    expect(done.state).toBe("failed");
    expect(done.recordWritten).toBe(true);
    expect(done.armedToday).toBe(true);
    expect(done.message).toMatch(/reads as ARMED/);
    expect(done.message).toMatch(/Nothing is ticking yet/);
  });

  it("an armed loop gets no token, and a token minted before it was armed is stale", async () => {
    installRunner(() => outcome({ ok: true }));
    const early = await readout("meic");
    meic = { armed_for: TODAY };
    const r = await readout("meic");
    expect(r.armedToday).toBe(true);
    expect(r.dialogId).toBeNull();
    const res = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: early.dialogId });
    expect(res.statusCode).toBe(409);
    expect(runs).toEqual([]);
  });

  it("opening an attempt for a loop that is armed already is a success with nothing run", () => {
    const { attempt, started } = beginAttempt(config, "meic", TODAY, true);
    expect(started).toBe(false);
    expect(attempt).toMatchObject({ state: "done", noop: true });
    expect(runs).toEqual([]);
  });

  it("one attempt in flight per loop: a second confirm gets the first attempt, and the command runs once", async () => {
    let release: () => void = () => undefined;
    installRunner(
      () =>
        new Promise<RunResult>((resolve) => {
          release = () => {
            meic = { armed_for: TODAY };
            resolve(outcome({ ok: true, armed_for: TODAY }));
          };
        }),
    );
    writeJobs(future());
    const a = await readout("meic");
    const first = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: a.dialogId });
    const b = await readout("meic");
    const second = await post("/api/live/arming/meic/arm", { confirm: "YES", dialogId: b.dialogId });
    expect((second.json() as { attempt: ArmAttempt }).attempt.id).toBe((first.json() as { attempt: ArmAttempt }).attempt.id);
    expect(runs).toHaveLength(1);
    release();
    await settled((first.json() as { attempt: ArmAttempt }).attempt.id);
  });

  it("a console that restarts mid-arm resolves the attempt from the status and never runs the command again", async () => {
    installRunner(() => outcome({ ok: true }));
    fs.mkdirSync(path.join(tmp, "console"), { recursive: true });
    const stuck: ArmAttempt = {
      id: "left-running",
      loop: "meic",
      session: TODAY,
      startedAt: new Date().toISOString(),
      finishedAt: null,
      state: "running",
      step: "waiting",
      recordWritten: true,
      needsSupervisor: true,
      noop: false,
      message: "waiting",
      detail: [],
      armedToday: null,
    };
    fs.writeFileSync(path.join(tmp, "console", "live-arming.json"), JSON.stringify({ attempts: [stuck] }));
    resetAttempts();
    meic = { armed_for: TODAY };
    await recoverAttempts(config);
    expect(listAttempts(config)[0]).toMatchObject({ id: "left-running", state: "done", armedToday: true });
    expect(runs).toEqual([]);
  });
});

describe("disarm", () => {
  it("runs the module's --disarm after a plain confirm, and only then", async () => {
    installRunner(() => outcome({ ok: true, arm_record_removed: true, detail: "disarmed (arm record removed)" }));
    expect((await post("/api/live/arming/meic/disarm", {})).statusCode).toBe(400);
    expect((await post("/api/live/arming/meic/disarm", { disarm: "true" })).statusCode).toBe(400);
    expect(runs).toEqual([]);
    const res = await post("/api/live/arming/ladder/disarm", { disarm: true });
    expect(res.json()).toMatchObject({ ok: true, removed: true });
    expect(runs).toEqual([["-m", "cherrypick.ladder.live_loop", "--disarm"]]);
  });

  it("an unarmed loop's `nothing was armed` is an answer, not an error", async () => {
    installRunner(() => outcome({ ok: false, arm_record_removed: false, detail: "nothing was armed" }, 1));
    const res = await post("/api/live/arming/meic/disarm", { disarm: true });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, removed: false, detail: "nothing was armed" });
  });
});

describe("the module's output", () => {
  it("reads the document after the disclaimer, and a refusal's exit 1", () => {
    const disclaimer = "# Disclaimer\n\nReal money. {braces} in prose.\n";
    expect(parseCommandJson(`${disclaimer}{\n  "ok": false,\n  "error": "no"\n}\n`)).toEqual({ ok: false, error: "no" });
    expect(parseCommandJson('{"ok": true}')).toEqual({ ok: true });
    expect(parseCommandJson("nothing json here")).toBeNull();
  });

  it("the argv is fixed: the literal word, the person's flag, never the agent's", () => {
    expect(armArgv("meic")).toEqual(["-m", "cherrypick.meic.live_loop", "--arm", "--confirm", "YES"]);
    expect(armArgv("ladder")).toEqual(["-m", "cherrypick.ladder.live_loop", "--arm", "--confirm", "YES"]);
    expect(armArgv("meic")).not.toContain("--install-task");
  });

  it("the real runner refuses to run under the test runner", async () => {
    setArmRunner();
    const out = await runArm("meic");
    expect(out).toMatchObject({ ok: false, failed: true });
    expect(out.error).toMatch(/refused under the test runner/);
  });
});
