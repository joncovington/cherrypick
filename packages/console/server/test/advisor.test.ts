import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { readAdvisor } from "../src/readers/advisor.js";
import { registerAdvisorRoutes } from "../src/routes/advisor.js";
import { registerAdvisorOpsRoutes } from "../src/routes/advisorOps.js";
import { setAdvisorCaller, type AdvisorOp } from "../src/services/advisorBridge.js";

/**
 * The Advisor page's server half.
 *
 * Three claims are worth a test. An advisor that has never run renders an empty page rather than a
 * 500 (the same posture review takes — a surface that breaks on a fresh machine is a surface nobody
 * trusts). A rejected proposal reaches the browser WITH its reason, because a rejection nobody sees
 * gets re-proposed forever. And the two write actions hold no logic here: they invoke the advisor's
 * own CLI and pass its refusal through with its meaning intact.
 */

let config: ConsoleConfig;
let app: FastifyInstance;
let tmp: string;
let seen: AdvisorOp[] = [];

const SESSION = "2026-08-13";
const NEXT = "2026-08-14";

/** Stands in for the process, not for the reply handling — the classification below is the real
 *  one, so "unknown id" and "the bridge is broken" are told apart by the code that ships. */
function fakeCli(payload: Record<string, unknown>, status = 0) {
  setAdvisorCaller((op) => {
    seen.push(op);
    return { status, stdout: JSON.stringify(payload), stderr: "" };
  });
}

// An empty JSON body, because the mutating-surface guard requires the content type on every POST.
const post = (url: string) =>
  app.inject({
    method: "POST",
    url,
    payload: {},
    headers: { host: "127.0.0.1:5070", "x-csrf-token": CSRF_TOKEN },
  });

function seedStore(): void {
  fs.mkdirSync(path.join(tmp, "advisor"), { recursive: true });
  const db = new Database(path.join(tmp, "advisor", "advisor.db"));
  db.exec(`
    CREATE TABLE checkpoints (id INTEGER PRIMARY KEY, session TEXT, slot TEXT, model TEXT, ok INTEGER,
      error TEXT, pack_path TEXT, raw_path TEXT, observations_json TEXT, flags_json TEXT, created_at TEXT);
    CREATE TABLE proposals (id INTEGER PRIMARY KEY, checkpoint_id INTEGER, module TEXT, kind TEXT,
      payload_json TEXT, status TEXT, reject_reason TEXT, experiment_id TEXT, created_at TEXT);
    CREATE TABLE experiments (id TEXT PRIMARY KEY, module TEXT, base_profile TEXT, name TEXT,
      hypothesis TEXT, success_metric TEXT, params_json TEXT, bounds_snapshot_json TEXT, status TEXT,
      created_session TEXT, expires_after_sessions INTEGER, sessions_run INTEGER,
      origin_proposal_id INTEGER, verdict_json TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE experiment_events (id INTEGER PRIMARY KEY, experiment_id TEXT, session TEXT,
      event TEXT, detail_json TEXT, created_at TEXT);
  `);
  db.prepare(
    "INSERT INTO checkpoints (id, session, slot, model, ok, observations_json, flags_json, created_at)" +
      " VALUES (1, ?, 'deep', 'opus', 1, ?, ?, ?)",
  ).run(
    SESSION,
    JSON.stringify(["control took no stops all session"]),
    JSON.stringify([{ module: "flies", severity: "warn", text: "completion rate halved" }]),
    `${SESSION}T21:05:00+00:00`,
  );
  db.prepare(
    "INSERT INTO proposals (id, checkpoint_id, module, kind, payload_json, status, reject_reason, created_at)" +
      " VALUES (7, 1, 'meic', 'bounded_adjustment', ?, 'rejected', ?, ?)",
  ).run(
    JSON.stringify({ kind: "bounded_adjustment", params: [{ param: "stop_trigger_ratio", value: 2 }] }),
    "1 proposal(s) violated advice_bounds (reject-all)",
    `${SESSION}T21:05:00+00:00`,
  );
  db.prepare(
    "INSERT INTO experiments (id, module, base_profile, name, params_json, status, created_session," +
      " expires_after_sessions, sessions_run, verdict_json, created_at, updated_at)" +
      " VALUES ('exp-1', 'meic', 'control', 'wider stop', ?, 'active', ?, 15, 3, ?, ?, ?)",
  ).run(
    JSON.stringify({ stop_trigger_ratio: 0.9 }),
    SESSION,
    JSON.stringify({
      pairs: [
        {
          advisedTag: "advised:control",
          baseTag: "control",
          advised: { net_pnl: 210, sample: 3, days: 3, win_rate: 0.66 },
          base: { net_pnl: 180, sample: 3, days: 3, win_rate: 0.66 },
          delta: { net_pnl: 30 },
          qualification: {},
          underpowered: true,
        },
      ],
      underpowered: true,
      recommendation: null,
    }),
    `${SESSION}T21:05:00+00:00`,
    `${SESSION}T21:05:00+00:00`,
  );
  db.prepare(
    "INSERT INTO experiment_events (experiment_id, session, event, detail_json, created_at)" +
      " VALUES ('exp-1', ?, 'enacted', ?, ?)",
  ).run(SESSION, JSON.stringify({ target: NEXT, written: true }), `${SESSION}T22:00:00+00:00`);
  db.close();
}

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-advisor-test-"));
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
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
  app = Fastify();
  registerSecurity(app);
  registerAdvisorRoutes(app, config);
  registerAdvisorOpsRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setAdvisorCaller();
  seen = [];
});

describe("the advisor reader", () => {
  it("renders an empty page before the advisor has ever run", () => {
    const payload = readAdvisor(config);
    expect(payload.storePresent).toBe(false);
    expect(payload.sessions).toEqual([]);
    expect(payload.experiments).toEqual([]);
    // The apply banner still lists every module, saying why each is not accepting advice.
    expect(payload.applyStatus.map((s) => s.module)).toEqual([
      "meic",
      "flies",
      "earnings",
      "calendars",
      "pmcc",
    ]);
    expect(payload.applyStatus[0]?.disabledReason).toContain("no deployed config");
  });

  it("reads checkpoints, proposals and experiments once there is a store", () => {
    seedStore();
    const payload = readAdvisor(config);
    expect(payload.storePresent).toBe(true);
    expect(payload.sessions).toEqual([SESSION]);
    expect(payload.latest[0]).toMatchObject({ slot: "deep", ok: true, model: "opus" });
    expect(payload.latest[0]?.flags[0]?.text).toBe("completion rate halved");
    expect(payload.experiments[0]).toMatchObject({ id: "exp-1", status: "active", sessionsRun: 3 });
    expect(payload.experiments[0]?.journal[0]?.event).toBe("enacted");
  });

  it("keeps a rejection's reason, because one nobody sees gets re-proposed forever", () => {
    const proposal = readAdvisor(config).proposals[0];
    expect(proposal).toMatchObject({ id: 7, status: "rejected" });
    expect(proposal?.rejectReason).toContain("reject-all");
  });

  it("says underpowered rather than passed or failed", () => {
    expect(readAdvisor(config).experiments[0]?.verdict?.underpowered).toBe(true);
  });

  it("separates 'an artifact was written' from 'the loop applied it'", () => {
    fs.mkdirSync(config.paths.adviceDir, { recursive: true });
    fs.writeFileSync(
      path.join(config.paths.adviceDir, `meic-${NEXT}.json`),
      JSON.stringify({
        module: "meic",
        session: NEXT,
        proposals: [{ param: "stop_trigger_ratio", value: 0.9, rationale: "wider" }],
        rejected: [],
      }),
    );
    const meic = readAdvisor(config).applyStatus.find((s) => s.module === "meic");
    expect(meic?.nextSession).toBe(NEXT);
    expect(meic?.artifactWritten).toBe(true);
    expect(meic?.artifactProposals[0]?.param).toBe("stop_trigger_ratio");
    // Written, but no loop has read it yet — two facts, kept apart.
    expect(meic?.consumerDecision).toBeNull();

    fs.mkdirSync(path.join(tmp, "data", "meic"), { recursive: true });
    fs.mkdirSync(config.paths.meicDir, { recursive: true });
    fs.writeFileSync(
      path.join(tmp, "data", "meic", "advice_active.json"),
      JSON.stringify({ day: NEXT, params: { stop_trigger_ratio: 0.9 }, reason: null }),
    );
    expect(readAdvisor(config).applyStatus.find((s) => s.module === "meic")?.consumerDecision).toMatchObject({
      day: NEXT,
    });
  });

  it("survives a corrupt artifact rather than taking the page down", () => {
    fs.writeFileSync(path.join(config.paths.adviceDir, `flies-${NEXT}.json`), "{ half written");
    const flies = readAdvisor(config).applyStatus.find((s) => s.module === "flies");
    expect(flies?.artifactWritten).toBe(false);
  });
});

describe("the advisor's two write actions", () => {
  it("kills an experiment through the advisor's own CLI and reports what took its slot", async () => {
    fakeCli({ ok: true, experiment_id: "exp-1", status: "killed", activated: ["exp-2"] });
    const res = await post("/api/advisor/experiments/exp-1/kill");
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "kill", experimentId: "exp-1" }]);
    expect(res.json()["activated"]).toEqual(["exp-2"]);
  });

  it("dismisses a proposal by id", async () => {
    fakeCli({ ok: true, proposal_id: 7 });
    const res = await post("/api/advisor/proposals/7/dismiss");
    expect(res.statusCode).toBe(200);
    expect(seen).toEqual([{ op: "dismiss", proposalId: 7 }]);
  });

  it("passes a refusal through as a 404, not as a broken bridge", async () => {
    fakeCli({ ok: false, reason: "no such experiment 'exp-nope'" }, 1);
    const res = await post("/api/advisor/experiments/exp-nope/kill");
    expect(res.statusCode).toBe(404);
    expect(res.json()["error"]).toContain("no such experiment");
  });

  it("rejects a non-numeric proposal id before it reaches a subprocess", async () => {
    fakeCli({ ok: true });
    const res = await post("/api/advisor/proposals/not-a-number/dismiss");
    expect(res.statusCode).toBe(400);
    expect(seen).toEqual([]);
  });

  it("needs the CSRF header like every other mutating surface", async () => {
    fakeCli({ ok: true });
    const res = await app.inject({
      method: "POST",
      url: "/api/advisor/experiments/exp-1/kill",
      headers: { host: "127.0.0.1:5070" },
    });
    expect(res.statusCode).toBe(403);
    expect(seen).toEqual([]);
  });
});
