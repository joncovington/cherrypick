import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity, CSRF_TOKEN } from "../src/security.js";
import { readAdvisor, readAdvisorModule } from "../src/readers/advisor.js";
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
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
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
    // Nothing has declared an advice block, so the banner covers nothing. An empty apply banner and
    // a banner listing five modules that do not exist are different claims, and only one is true.
    expect(payload.applyStatus).toEqual([]);
  });

  it("covers a module the moment its config declares an advice block", () => {
    // bwb had been enacting its artifact every session since 2026-08-28 and appeared nowhere on this
    // page, because the module list was a hand-kept constant of five. Discovery is the fix: declare
    // the block, and the module is covered — no second place to remember.
    const dir = path.join(tmp, "config");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "bwb.json"),
      JSON.stringify({ advice: { enabled: true, bounds: { flip_buffer: { min: 1.0001, max: 1.05 } } } }),
    );
    // Declared and switched off is still coverage — `disabledReason` is what says which.
    fs.writeFileSync(path.join(dir, "curve.json"), JSON.stringify({ advice: { enabled: false, bounds: {} } }));
    // No advice block at all, and a backup that must not become a module of its own.
    fs.writeFileSync(path.join(dir, "gex.json"), JSON.stringify({ symbols: ["SPX"] }));
    fs.writeFileSync(path.join(dir, "bwb.json.bak-20260823"), JSON.stringify({ advice: { enabled: true } }));

    const status = readAdvisor(config).applyStatus;
    expect(status.map((s) => s.module)).toEqual(["bwb", "curve"]);
    expect(status.find((s) => s.module === "bwb")?.disabledReason).toBeNull();
    expect(status.find((s) => s.module === "curve")?.disabledReason).toContain("advice.enabled is false");
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
    // No `tag` column on this store: the book name is derived from the experiment's name, the
    // same slug rule cherrypick.core.advice.slug applies.
    expect(payload.experiments[0]?.tag).toBe("advised:wider-stop");
  });

  it("reads a store that predates the model_id column, and the id once the producer has added it", () => {
    // The store seeded by the test above persists for the rest of this describe (one home per file).
    expect(readAdvisor(config).latest[0]).toMatchObject({ model: "opus", modelId: null });

    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.exec("ALTER TABLE checkpoints ADD COLUMN model_id TEXT");
    db.prepare("UPDATE checkpoints SET model_id = ? WHERE id = 1").run("claude-opus-5");
    db.close();
    expect(readAdvisor(config).latest[0]).toMatchObject({ model: "opus", modelId: "claude-opus-5" });
  });

  it("surfaces a stalled conclusion, and reads null where an older verdict never wrote one", () => {
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.prepare(
      "INSERT INTO experiments (id, module, base_profile, params_json, status, created_session," +
        " expires_after_sessions, sessions_run, verdict_json, created_at, updated_at)" +
        " VALUES ('exp-stalled', 'meic', 'control', '{}', 'expired', ?, 15, 2, ?, ?, ?)",
    ).run(
      SESSION,
      JSON.stringify({ pairs: [], underpowered: true, stalled: { sessions_run: 2, calendar_sessions: 31 } }),
      `${SESSION}T21:05:00+00:00`,
      `${SESSION}T21:05:00+00:00`,
    );
    db.close();
    const payload = readAdvisor(config);
    const stalled = payload.experiments.find((e) => e.id === "exp-stalled");
    expect(stalled?.verdict?.stalled).toEqual({ sessionsRun: 2, calendarSessions: 31 });
    const other = payload.experiments.find((e) => e.id !== "exp-stalled" && e.verdict !== null);
    expect(other?.verdict?.stalled ?? null).toBeNull();
    // Leave the seeded store as the tests below expect it.
    const cleanup = new Database(path.join(tmp, "advisor", "advisor.db"));
    cleanup.prepare("DELETE FROM experiments WHERE id = 'exp-stalled'").run();
    cleanup.close();
  });

  it("gives a module its own view: the active experiments, their strips, its queue and tomorrow", () => {
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    // The 2026-09-17 key: one row per experiment per session.
    db.exec(
      "CREATE TABLE IF NOT EXISTS enactment (session TEXT, module TEXT, status TEXT, detail TEXT," +
        " experiment_id TEXT, artifact_params TEXT, decision_params TEXT, decision_reason TEXT, scored_at TEXT," +
        " PRIMARY KEY (session, module, experiment_id))",
    );
    const put = db.prepare(
      "INSERT OR REPLACE INTO enactment (session, module, status, detail, experiment_id) VALUES (?, 'meic', ?, ?, ?)",
    );
    put.run("2026-08-11", "enacted", "matched", "exp-1");
    put.run("2026-08-12", "carried", "frozen on open rows", "exp-1");
    put.run(SESSION, "not_enacted", "the loop recorded no decision", "exp-1");
    // A second active experiment on the same base, with its own tag column and its own rows.
    db.exec("ALTER TABLE experiments ADD COLUMN tag TEXT");
    db.prepare(
      "INSERT INTO experiments (id, module, base_profile, name, tag, params_json, status, created_session," +
        " expires_after_sessions, sessions_run, created_at, updated_at)" +
        " VALUES ('exp-2', 'meic', 'control', 'later entry', 'advised:later-entry-v2', '{\"b\": 2}', 'active', '2026-08-12', 10, 1, ?, ?)",
      // Activated after exp-1 (created_at is the activation order) though its created_session is
      // earlier -- what the calendar-age count keys on.
    ).run(`${SESSION}T21:07:00+00:00`, `${SESSION}T21:07:00+00:00`);
    put.run(SESSION, "enacted", null, "exp-2");
    db.prepare(
      "INSERT INTO experiments (id, module, base_profile, params_json, status, created_session," +
        " expires_after_sessions, sessions_run, created_at, updated_at)" +
        " VALUES ('exp-q', 'meic', 'control', '{\"a\": 1}', 'queued', ?, 15, 0, ?, ?)",
    ).run(SESSION, `${SESSION}T21:06:00+00:00`, `${SESSION}T21:06:00+00:00`);
    db.close();

    const view = readAdvisorModule(config, "meic");
    expect(view.storePresent).toBe(true);
    expect(view.active.map((e) => e.id)).toEqual(["exp-1", "exp-2"]);
    // The stored tag column wins over the slug of the name.
    expect(view.active[1]?.tag).toBe("advised:later-entry-v2");
    expect(view.queued.map((q) => q.id)).toEqual(["exp-q"]);
    // Every row over the last scored sessions, so each experiment's strip can be drawn.
    expect(view.sessions.map((s) => [s.session, s.experimentId, s.status])).toEqual([
      ["2026-08-11", "exp-1", "enacted"],
      ["2026-08-12", "exp-1", "carried"],
      [SESSION, "exp-1", "not_enacted"],
      [SESSION, "exp-2", "enacted"],
    ]);
    // Progress is per experiment: each against its own length and its own calendar age.
    expect(view.active[0]).toMatchObject({ stallBudget: 30, calendarSessions: 0 });
    expect(view.active[1]).toMatchObject({ stallBudget: 20, calendarSessions: 1 });
    expect(view.tomorrow?.module).toBe("meic");
    // The chosen session's enactment comes back per experiment on the apply status too.
    expect(view.tomorrow?.enactments.map((e) => [e.experimentId, e.status])).toEqual([
      ["exp-1", "not_enacted"],
      ["exp-2", "enacted"],
    ]);

    // A module the advisor has never touched is an honest empty view, not an error.
    const none = readAdvisorModule(config, "curve");
    expect(none.active).toEqual([]);
    expect(none.sessions).toEqual([]);

    // Leave the seeded store as the tests below expect it: no queued or second row, and no
    // enactment table (one of them asserts the reader degrades on a store that predates it).
    const cleanup = new Database(path.join(tmp, "advisor", "advisor.db"));
    cleanup.prepare("DELETE FROM experiments WHERE id IN ('exp-q', 'exp-2')").run();
    cleanup.exec("DROP TABLE enactment");
    cleanup.close();
  });

  it("serves a module's view on its own route", async () => {
    const res = await app.inject({ method: "GET", url: "/api/advisor/module/meic" });
    expect(res.statusCode).toBe(200);
    expect(res.json().module).toBe("meic");
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
    const landed = readAdvisor(config).applyStatus.find((s) => s.module === "meic");
    expect(landed?.consumerDecision).toMatchObject({ day: NEXT });
    // A legacy flat decision reads as one experiment entry; a legacy artifact likewise.
    expect(landed?.decisionExperiments).toEqual([
      { experimentId: null, name: null, tag: null, base: null, params: { stop_trigger_ratio: 0.9 }, reason: null },
    ]);
    expect(landed?.artifactExperiments).toHaveLength(1);
    expect(landed?.artifactExperiments[0]?.proposals[0]?.param).toBe("stop_trigger_ratio");
  });

  it("reads the per-experiment lists of an artifact and a decision, never the legacy mirror beside them", () => {
    // 2026-09-17: `experiments` carries one entry per concurrent experiment and the top-level
    // proposals/params mirror the FIRST. Reading both would count that experiment twice.
    fs.writeFileSync(
      path.join(config.paths.adviceDir, `meic-${NEXT}.json`),
      JSON.stringify({
        module: "meic",
        session: NEXT,
        experiment_id: "exp-1",
        proposals: [{ param: "stop_trigger_ratio", value: 0.9, rationale: "wider" }],
        rejected: [],
        experiments: [
          { experiment_id: "exp-1", name: "wider stop", tag: "advised:wider-stop", base: "control", proposals: [{ param: "stop_trigger_ratio", value: 0.9, rationale: "wider" }], rejected: [] },
          { experiment_id: "exp-2", name: "later entry", tag: "advised:later-entry", base: "control", proposals: [], rejected: [{ param: "entry_window_end", value: "15:59", reason: "above max" }] },
        ],
      }),
    );
    fs.writeFileSync(
      path.join(tmp, "data", "meic", "advice_active.json"),
      JSON.stringify({
        day: NEXT,
        params: { stop_trigger_ratio: 0.9 },
        experiment_id: "exp-1",
        reason: null,
        experiments: [
          { experiment_id: "exp-1", name: "wider stop", tag: "advised:wider-stop", base: "control", params: { stop_trigger_ratio: 0.9 }, reason: null },
          { experiment_id: "exp-2", name: "later entry", tag: "advised:later-entry", base: "control", params: {}, reason: "reject-all" },
        ],
      }),
    );
    const meic = readAdvisor(config).applyStatus.find((s) => s.module === "meic");
    expect(meic?.artifactExperiments.map((e) => [e.experimentId, e.tag, e.proposals.length, e.rejected.length])).toEqual([
      ["exp-1", "advised:wider-stop", 1, 0],
      ["exp-2", "advised:later-entry", 0, 1],
    ]);
    expect(meic?.decisionExperiments.map((e) => [e.experimentId, e.base, e.reason])).toEqual([
      ["exp-1", "control", null],
      ["exp-2", "control", "reject-all"],
    ]);
    // The mirror fields still read as before for anything written against the old shape.
    expect(meic?.artifactProposals[0]?.param).toBe("stop_trigger_ratio");
  });

  it("degrades when the store predates the enactment table", () => {
    // seedStore (run by an earlier test in this file) builds the PRE-enactment schema on purpose:
    // a machine that has not run the current advisor build is the ordinary case, and the page must
    // render without the column rather than 500.
    const meic = readAdvisor(config).applyStatus.find((s) => s.module === "meic");
    expect(meic?.enactments).toEqual([]);
  });

  it("surfaces the advisor's verdict that an artifact never reached its loop", () => {
    // The 2026-08-25 incident, as a payload. Two modules held a live, valid artifact and their
    // loops recorded `advice_disabled` against it; the page showed "written" and said nothing.
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.exec(
      "CREATE TABLE enactment (session TEXT, module TEXT, status TEXT, detail TEXT," +
        " experiment_id TEXT, artifact_params TEXT, decision_params TEXT, decision_reason TEXT," +
        " scored_at TEXT, PRIMARY KEY (session, module))",
    );
    db.prepare(
      "INSERT INTO enactment (session, module, status, detail, experiment_id, decision_reason," +
        " scored_at) VALUES (?,?,?,?,?,?,?)",
    ).run(SESSION, "meic", "not_enacted", "the loop recorded {} against an artifact admitting" +
      " {'stop_trigger_ratio': 0.9}", "exp-1", "advice_disabled", `${SESSION}T21:05:00+00:00`);
    db.prepare(
      "INSERT INTO enactment (session, module, status, experiment_id, scored_at) VALUES (?,?,?,?,?)",
    ).run(SESSION, "flies", "enacted", "exp-2", `${SESSION}T21:05:00+00:00`);
    db.close();

    const status = readAdvisor(config).applyStatus;
    const meic = status.find((s) => s.module === "meic");
    expect(meic?.enactments).toHaveLength(1);
    expect(meic?.enactments[0]).toMatchObject({ status: "not_enacted", decisionReason: "advice_disabled" });
    expect(meic?.enactments[0]?.detail).toContain("stop_trigger_ratio");
    expect(status.find((s) => s.module === "flies")?.enactments[0]?.status).toBe("enacted");
    // A module the advisor never scored is not a failure and must not borrow one. Declared, so it
    // is genuinely on the banner — an absent module would pass this by not being there at all.
    fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
    fs.writeFileSync(path.join(tmp, "config", "pmcc.json"), JSON.stringify({ advice: { enabled: true } }));
    const pmcc = readAdvisor(config).applyStatus.find((s) => s.module === "pmcc");
    expect(pmcc).toBeDefined();
    expect(pmcc?.enactments).toEqual([]);
  });

  it("fills in a verdict field an older row never wrote", () => {
    // A real 08-26 experiment row carries a verdict with no `recommendation` key at all. The reader
    // used to cast the parse straight to AdvisorVerdict, and the page's `!== null` check let the
    // resulting `undefined` through into `.value` — which blanked the whole experiments tab.
    const store = new Database(path.join(tmp, "advisor", "advisor.db"));
    store.prepare(
      "INSERT INTO experiments (id, module, base_profile, name, params_json, status," +
        " created_session, expires_after_sessions, sessions_run, verdict_json, created_at, updated_at)" +
        " VALUES ('exp-old', 'flies', 'control', 'narrow wing', '{}', 'killed', ?, 10, 4, ?, ?, ?)",
    ).run(SESSION, JSON.stringify({ pairs: [], underpowered: true }), SESSION, SESSION);
    store.close();
    const old = readAdvisor(config).experiments.find((e) => e.id === "exp-old");
    expect(old?.verdict?.recommendation).toBeNull();
    expect(old?.verdict?.pairs).toEqual([]);
  });

  it("reads the verdict pair's tags under the advisor's own snake_case spelling", () => {
    // Every real verdict names its books as `advised_tag`/`base_tag`, and the page's Book column
    // rendered blank against the camelCase the type promised. The seed above is camelCase and
    // still reads; this row is the shape the advisor actually writes.
    const store = new Database(path.join(tmp, "advisor", "advisor.db"));
    store.prepare(
      "INSERT INTO experiments (id, module, base_profile, name, params_json, status," +
        " created_session, expires_after_sessions, sessions_run, verdict_json, created_at, updated_at)" +
        " VALUES ('exp-snake', 'bwb', 'control', 'flip-buffer-near-control', '{}', 'active', ?, 15, 1, ?, ?, ?)",
    ).run(
      SESSION,
      JSON.stringify({
        pairs: [
          {
            module: "bwb",
            advised_tag: "advised:flip-buffer-near-control",
            base_tag: "control",
            advised: { net_pnl: 1 },
            base: { net_pnl: 2 },
            delta: { net_pnl: -1 },
            qualification: { "advised:flip-buffer-near-control": { qualified: false } },
            underpowered: true,
          },
        ],
        underpowered: true,
      }),
      SESSION,
      SESSION,
    );
    store.close();
    const payload = readAdvisor(config);
    const snake = payload.experiments.find((e) => e.id === "exp-snake")?.verdict?.pairs[0];
    expect(snake).toMatchObject({ advisedTag: "advised:flip-buffer-near-control", baseTag: "control" });
    expect(snake?.qualification["advised:flip-buffer-near-control"]).toEqual({ qualified: false });
    expect(payload.experiments.find((e) => e.id === "exp-1")?.verdict?.pairs[0]?.advisedTag).toBe("advised:control");
    const cleanup = new Database(path.join(tmp, "advisor", "advisor.db"));
    cleanup.prepare("DELETE FROM experiments WHERE id = 'exp-snake'").run();
    cleanup.close();
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
