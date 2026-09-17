/**
 * The AI advisor: read what it observed, proposed and ran — and compute nothing.
 *
 * `packages/advisor` owns every judgement on this page. Its verdicts come from the suite's own
 * chain (ledger readers → `compare_profiles` → `qualify_readings`), the same one calibration and
 * the review use, and they are stored on the experiment row. This reader parses, shapes and passes
 * through. A TypeScript re-derivation would be a second opinion free to drift from the first, which
 * is exactly the mistake `services/report.ts` already made once with the P&L rules.
 *
 * Everything degrades to empty. Before the advisor has ever run there is no database, no packs and
 * no artifacts, and the page must render an empty state rather than a 500 — the same defensive
 * posture `review.ts` takes, for the same reason: a surface that breaks when a module has not run
 * yet is a surface nobody trusts on a fresh machine.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  AdvisorActiveExperiment,
  AdvisorApplyStatus,
  AdvisorArtifactExperiment,
  AdvisorCheckpoint,
  AdvisorDecisionExperiment,
  AdvisorEnactment,
  AdvisorEvent,
  AdvisorExperiment,
  AdvisorFlag,
  AdvisorModulePayload,
  AdvisorPayload,
  AdvisorProposal,
  AdvisorSessionCell,
  AdvisorVerdict,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import type Database from "better-sqlite3";
import { hasTable, readJson, str, withReadOnlyDb } from "./db.js";
import { tagOfRow } from "./experimentIndex.js";

/** The advisor's own slot order — chronological, not alphabetical. Mirrors
 * packages/advisor/src/cherrypick/advisor/factpack.py's LIGHT_SLOTS + DEEP_SLOT. */
const SLOT_ORDER = ["open", "am1", "am2", "midday", "pm1", "pm2", "close", "deep"];
/**
 * The advisor's declared reach, in the order the suite's own configs are usually read.
 *
 * Only an ORDERING hint. Which modules the apply banner covers is discovered, not listed here —
 * see `adviceModules`. A module named here that declares nothing is not shown; one absent from
 * here that declares an advice block is, sorted after the names it does know.
 */
const MODULE_ORDER = ["meic", "flies", "earnings", "calendars", "pmcc", "bwb", "curve"];
const HISTORY_LIMIT = 40;

function parse<T>(raw: unknown, fallback: T): T {
  if (typeof raw !== "string" || raw === "") return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/**
 * A stored verdict, with every field the type promises actually present.
 *
 * `verdict_json` is whatever `packages/advisor` wrote at the time, and older rows predate fields
 * the current shape declares — one 08-26 row carries no `recommendation` key at all. Casting the
 * parse to `AdvisorVerdict` made that a lie the compiler could not see, and the console read
 * `verdict.recommendation.value` past a `!== null` check that `undefined` walks straight through,
 * blanking the advisor page's experiments tab. Normalize here, once, rather than defending against
 * the same absence at every read site.
 */
function verdict(raw: unknown): AdvisorVerdict | null {
  const v = parse<Partial<AdvisorVerdict> | null>(raw, null);
  if (v === null || typeof v !== "object") return null;
  const body = v as Record<string, unknown>;
  const stalledRaw = body["stalled"];
  const stalled =
    stalledRaw !== null && typeof stalledRaw === "object"
      ? {
          sessionsRun: Number((stalledRaw as Record<string, unknown>)["sessions_run"] ?? 0),
          calendarSessions: Number((stalledRaw as Record<string, unknown>)["calendar_sessions"] ?? 0),
        }
      : null;
  // `verdicts.py` writes the pair's tags as `advised_tag`/`base_tag`; the page reads them as
  // `advisedTag`/`baseTag` and its "Book" column rendered blank on every real verdict (found
  // 2026-09-17, when the column became the place each experiment's own book is named). Both
  // spellings read; the rest of the pair -- readings, delta, qualification keyed by tag -- is
  // passed through verbatim, as the reader's header promises.
  const pairs = (Array.isArray(v.pairs) ? v.pairs : []).map((p) => {
    const raw = p as unknown as Record<string, unknown>;
    return {
      ...p,
      advisedTag: str(raw["advisedTag"]) ?? str(raw["advised_tag"]) ?? "",
      baseTag: str(raw["baseTag"]) ?? str(raw["base_tag"]) ?? "",
    };
  });
  return {
    pairs,
    underpowered: v.underpowered === true,
    recommendation: v.recommendation ?? null,
    stalled,
  };
}

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.advisorDir, "advisor.db");
}

interface CheckpointRow {
  session: string;
  slot: string;
  model: string | null;
  model_id: string | null;
  ok: number;
  error: string | null;
  observations_json: string | null;
  flags_json: string | null;
  created_at: string | null;
}

function shapeCheckpoint(row: CheckpointRow): AdvisorCheckpoint {
  return {
    session: row.session,
    slot: row.slot,
    model: row.model,
    modelId: row.model_id ?? null,
    ok: row.ok === 1,
    error: row.error,
    observations: parse<string[]>(row.observations_json, []),
    flags: parse<AdvisorFlag[]>(row.flags_json, []),
    createdAt: row.created_at,
  };
}

/**
 * Which modules the apply banner covers — read from what the suite itself declares.
 *
 * This was a hand-kept list of five, and it was wrong: bwb declares `advice.enabled` with bounds,
 * runs an active experiment, and has enacted its artifact every session since 2026-08-28 — none of
 * which reached this page, because bwb was never added to the constant. curve was invisible the
 * same way. That is the failure mode the suite's own working rules name: a list maintained beside
 * the thing it describes drifts the moment a module is added, and the drift reads as "this module
 * takes no advice" rather than as a missing entry.
 *
 * So discover it. A module is covered when its deployed config declares an `advice` block at all —
 * `enabled: false` and empty bounds are still coverage, because "declared and switched off" is a
 * fact worth showing and `disabledReason` already says which. Modules the advisor has ALREADY
 * scored are unioned in, so pulling a config hides the module's history rather than only its
 * config. Nothing declaring nothing is listed: on a fresh machine the banner is empty, which is
 * the honest reading.
 */
function adviceModules(config: ConsoleConfig, nextSession: string | null, scored: Iterable<string>): string[] {
  const found = new Set<string>(scored);
  // An artifact written for a module that declares nothing is exactly the drift worth seeing, so
  // the artifact counts as a declaration too — `disabledReason` then says the config is missing.
  if (nextSession !== null) {
    try {
      for (const entry of fs.readdirSync(config.paths.adviceDir)) {
        const suffix = `-${nextSession}.json`;
        if (entry.endsWith(suffix)) found.add(entry.slice(0, -suffix.length));
      }
    } catch {
      /* no advice directory yet */
    }
  }
  const dir = path.join(config.paths.cherrypick, "config");
  let entries: string[] = [];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    entries = [];
  }
  for (const entry of entries) {
    // Exactly `<module>.json`. The config directory is full of dated backups — `meic.json.pre-era`,
    // `pmcc.json.bak-20260824` — and each would otherwise become a module of its own.
    if (!entry.endsWith(".json")) continue;
    const module = entry.slice(0, -".json".length);
    if (module === "") continue;
    if (readJson(path.join(dir, entry))?.["advice"] !== undefined) found.add(module);
  }
  return [...found].sort((a, b) => {
    const ia = MODULE_ORDER.indexOf(a);
    const ib = MODULE_ORDER.indexOf(b);
    if (ia !== ib) return (ia === -1 ? MODULE_ORDER.length : ia) - (ib === -1 ? MODULE_ORDER.length : ib);
    return a.localeCompare(b);
  });
}

/**
 * What reached the loops for the next session, per module.
 *
 * Two facts, kept separate on purpose: the advisor WROTE an artifact, and the module's loop APPLIED
 * it. They come apart in ordinary operation — a module that stopped accepting advice, a loop that
 * has not started its session yet, a reject-all artifact that legitimately carries nothing — and
 * collapsing them into one "advice is on" badge would hide exactly the cases worth looking at.
 */
function readApplyStatus(
  config: ConsoleConfig,
  nextSession: string | null,
  enactment: Map<string, AdvisorEnactment[]>,
): AdvisorApplyStatus[] {
  return adviceModules(config, nextSession, enactment.keys()).map((module) => {
    const artifact =
      nextSession === null ? null : readJson(path.join(config.paths.adviceDir, `${module}-${nextSession}.json`));
    const decision = readJson(path.join(config.paths.cherrypick, "data", module, "advice_active.json"));
    const moduleConfig = readJson(path.join(config.paths.cherrypick, "config", `${module}.json`));
    const advice = (moduleConfig?.["advice"] ?? null) as Record<string, unknown> | null;

    let disabledReason: string | null = null;
    if (moduleConfig === null) disabledReason = "no deployed config for this module";
    else if (advice === null) disabledReason = "the module's config declares no advice block";
    else if (advice["enabled"] !== true) disabledReason = "advice.enabled is false in the module's config";
    else if (Object.keys((advice["bounds"] ?? {}) as object).length === 0)
      disabledReason = "advice.bounds is empty";

    return {
      module,
      nextSession,
      artifactWritten: artifact !== null,
      artifactProposals: (artifact?.["proposals"] ?? []) as AdvisorApplyStatus["artifactProposals"],
      artifactRejected: (artifact?.["rejected"] ?? []) as AdvisorApplyStatus["artifactRejected"],
      artifactExperiments: artifactExperiments(artifact),
      consumerDecision: decision,
      decisionExperiments: decisionExperiments(decision),
      disabledReason,
      enactments: enactment.get(module) ?? [],
    };
  });
}

/**
 * The per-experiment entries of an artifact or a decision file (2026-09-17), or the one entry the
 * legacy flat shape implies. Prefer the list; the top-level `proposals`/`params`/`experiment_id`
 * are a mirror of its first entry kept for readers written against the old shape, and reading
 * them beside the list would count the first experiment twice.
 */
function artifactExperiments(artifact: Record<string, unknown> | null): AdvisorArtifactExperiment[] {
  if (artifact === null) return [];
  const list = artifact["experiments"];
  const entries = Array.isArray(list) && list.length > 0 ? list : [artifact];
  return entries
    .filter((e): e is Record<string, unknown> => typeof e === "object" && e !== null)
    .map((e) => ({
      experimentId: str(e["experiment_id"]),
      name: str(e["name"]),
      tag: str(e["tag"]),
      base: str(e["base"]),
      proposals: (Array.isArray(e["proposals"]) ? e["proposals"] : []) as AdvisorArtifactExperiment["proposals"],
      rejected: (Array.isArray(e["rejected"]) ? e["rejected"] : []) as AdvisorArtifactExperiment["rejected"],
    }));
}

function decisionExperiments(decision: Record<string, unknown> | null): AdvisorDecisionExperiment[] {
  if (decision === null) return [];
  const list = decision["experiments"];
  if (Array.isArray(list)) {
    return list
      .filter((e): e is Record<string, unknown> => typeof e === "object" && e !== null)
      .map((e) => ({
        experimentId: str(e["experiment_id"]),
        name: str(e["name"]),
        tag: str(e["tag"]),
        base: str(e["base"]),
        params: (typeof e["params"] === "object" ? e["params"] : null) as Record<string, unknown> | null,
        reason: str(e["reason"]),
      }));
  }
  // The legacy single-experiment decision: params (or none) plus the id the loop stamped.
  const legacyBase = ["base_arm", "base_profile", "base_book", "base_prefix"]
    .map((k) => str(decision[k]))
    .find((v) => v !== null && v !== "");
  return [
    {
      experimentId: str(decision["experiment_id"]),
      name: null,
      tag: legacyBase === undefined ? null : `advised:${legacyBase}`,
      base: legacyBase ?? null,
      params: (typeof decision["params"] === "object" ? decision["params"] : null) as Record<string, unknown> | null,
      reason: str(decision["reason"]),
    },
  ];
}

/**
 * The advisor's stored enactment reconciliation for one session, keyed by module — every row
 * the module has for the session, since the table keys on (session, module, experiment_id)
 * (2026-09-17) and a module running two experiments gets two verdicts a session.
 *
 * Read, not computed. Whether a loop applied an artifact is decided once in `enactment.py` and
 * written to the `enactment` table; re-deriving it here from the artifact and the decision file
 * would be the second opinion this reader's own header warns about.
 *
 * Absent before the advisor has run a slot for the session, which the page renders as "not scored
 * yet" rather than as a failure — an unscored session and a dropped artifact are different facts.
 */
function readEnactment(db: Database.Database, session: string | null): Map<string, AdvisorEnactment[]> {
  const out = new Map<string, AdvisorEnactment[]>();
  // An advisor.db predating the table is the ordinary state of a machine that has not run the
  // current build yet, not an error: the page must render without the column rather than 500.
  if (session === null || !hasTable(db, "enactment")) return out;
  const rows = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT session, module, status, detail, experiment_id, decision_reason, scored_at" +
        " FROM enactment WHERE session = ? ORDER BY module, experiment_id",
    )
    .all(session);
  for (const r of rows) {
    const module = String(r["module"]);
    out.set(module, [
      ...(out.get(module) ?? []),
      {
        session: String(r["session"]),
        status: String(r["status"]),
        detail: str(r["detail"]),
        experimentId: str(r["experiment_id"]),
        decisionReason: str(r["decision_reason"]),
        scoredAt: str(r["scored_at"]),
      },
    ]);
  }
  return out;
}

/** One experiment row with its journal — the shape both the page and the module slide read. */
function shapeExperiment(db: Database.Database, r: Record<string, unknown>): AdvisorExperiment {
  const id = String(r["id"]);
  const journal: AdvisorEvent[] = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT session, event, detail_json, created_at FROM experiment_events" +
        " WHERE experiment_id = ? ORDER BY id",
    )
    .all(id)
    .map((e) => ({
      session: str(e["session"]),
      event: String(e["event"]),
      detail: parse<Record<string, unknown> | null>(e["detail_json"], null),
      createdAt: str(e["created_at"]),
    }));
  return {
    id,
    module: String(r["module"]),
    baseProfile: String(r["base_profile"]),
    name: str(r["name"]),
    tag: tagOfRow(r),
    hypothesis: str(r["hypothesis"]),
    successMetric: str(r["success_metric"]),
    params: parse<Record<string, unknown>>(r["params_json"], {}),
    status: String(r["status"]),
    createdSession: String(r["created_session"]),
    sessionsRun: Number(r["sessions_run"] ?? 0),
    expiresAfter: Number(r["expires_after_sessions"] ?? 0),
    verdict: verdict(r["verdict_json"]),
    journal,
  };
}

const SESSION_STRIP = 15;
const CONCLUDED_SHOWN = 3;

/**
 * One module's view of the advisor (2026-09-12): the experiments running on it — several at once
 * since 2026-09-17, each with its own progress and its own session strip — what is queued behind
 * them, and tomorrow's artifact. Read from the same store as the page; every judgement in it
 * (enactment status, verdict) is the advisor's own, never re-derived here.
 */
export function readAdvisorModule(config: ConsoleConfig, module: string): AdvisorModulePayload {
  const empty: AdvisorModulePayload = {
    module,
    storePresent: false,
    active: [],
    queued: [],
    concluded: [],
    sessions: [],
    tomorrow: null,
  };
  return withReadOnlyDb(dbPath(config), empty, (db): AdvisorModulePayload => {
    const rows = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT * FROM experiments WHERE module = ? ORDER BY created_at, id",
      )
      .all(module)
      .map((r) => shapeExperiment(db, r));
    const queued = rows.filter((e) => e.status === "queued");
    const concluded = rows
      .filter((e) => e.status === "expired" || e.status === "killed")
      .reverse()
      .slice(0, CONCLUDED_SHOWN);

    // The strip and each experiment's calendar age both come from the enactment table, which the
    // evening pass writes once per experiment per scored session — so "calendar sessions" here
    // means sessions the advisor scored, the same count its own stall exit uses. The strip takes
    // every row over the last SESSION_STRIP scored sessions rather than the last SESSION_STRIP
    // rows: two experiments would otherwise halve the strip's reach.
    let sessions: AdvisorSessionCell[] = [];
    const hasEnactment = hasTable(db, "enactment");
    if (hasEnactment) {
      sessions = db
        .prepare<[string, string, number], Record<string, unknown>>(
          "SELECT session, status, experiment_id, detail FROM enactment WHERE module = ? AND session IN" +
            " (SELECT DISTINCT session FROM enactment WHERE module = ? ORDER BY session DESC LIMIT ?)" +
            " ORDER BY session, experiment_id",
        )
        .all(module, module, SESSION_STRIP)
        .map((r) => ({
          session: String(r["session"]),
          status: String(r["status"]),
          experimentId: str(r["experiment_id"]),
          detail: str(r["detail"]),
        }));
    }
    const active: AdvisorActiveExperiment[] = rows
      .filter((e) => e.status === "active")
      .map((e) => {
        let calendarSessions: number | null = null;
        if (hasEnactment) {
          const row = db
            .prepare<[string, string], { n: number }>(
              "SELECT COUNT(DISTINCT session) AS n FROM enactment WHERE module = ? AND session > ?",
            )
            .get(module, e.createdSession);
          calendarSessions = Number(row?.n ?? 0);
        }
        return { ...e, calendarSessions, stallBudget: 2 * e.expiresAfter };
      });

    const scoredSessions = db
      .prepare<[], { session: string }>("SELECT DISTINCT session FROM checkpoints ORDER BY session")
      .all()
      .map((r) => r.session);
    const chosen = scoredSessions[scoredSessions.length - 1] ?? null;
    const nextSession = latestAdviceSession(config, chosen);
    const tomorrow =
      readApplyStatus(config, nextSession, readEnactment(db, chosen)).find((s) => s.module === module) ?? null;

    return {
      module,
      storePresent: true,
      active,
      queued,
      concluded,
      sessions,
      tomorrow,
    };
  });
}

export function readAdvisor(config: ConsoleConfig, session?: string): AdvisorPayload {
  const empty: AdvisorPayload = {
    sessions: [],
    session: session ?? null,
    latest: [],
    checkpoints: [],
    proposals: [],
    experiments: [],
    applyStatus: readApplyStatus(config, null, new Map()),
    storePresent: false,
  };

  return withReadOnlyDb(dbPath(config), empty, (db): AdvisorPayload => {
    const sessions = db
      .prepare<[], { session: string }>("SELECT DISTINCT session FROM checkpoints ORDER BY session")
      .all()
      .map((r) => r.session);
    const chosen = session !== undefined && sessions.includes(session) ? session : sessions[sessions.length - 1];

    // `model_id` arrived 2026-09-12 by the producer's additive migration; a store the advisor has
    // not opened since predates it, and this reader must keep answering on that store.
    const checkpointCols = new Set(
      db.prepare<[], { name: string }>("PRAGMA table_info(checkpoints)").all().map((c) => c.name),
    );
    const modelIdCol = checkpointCols.has("model_id") ? "model_id" : "NULL AS model_id";
    const history = db
      .prepare<[number], CheckpointRow>(
        `SELECT session, slot, model, ${modelIdCol}, ok, error, observations_json, flags_json, created_at` +
          " FROM checkpoints ORDER BY session DESC, created_at DESC LIMIT ?",
      )
      .all(HISTORY_LIMIT)
      .map(shapeCheckpoint);

    const latest = (chosen === undefined ? [] : history.filter((c) => c.session === chosen)).sort(
      (a, b) => SLOT_ORDER.indexOf(a.slot) - SLOT_ORDER.indexOf(b.slot),
    );

    // Every proposal from the chosen session, whatever became of it. A rejected proposal shown
    // WITH its reason is the whole point: it is what stops the same idea coming back tomorrow.
    const proposals: AdvisorProposal[] = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT p.id, p.module, p.kind, p.status, p.reject_reason, p.experiment_id, p.payload_json," +
          " p.created_at, c.session, c.slot FROM proposals p" +
          " LEFT JOIN checkpoints c ON c.id = p.checkpoint_id" +
          " WHERE c.session = ? ORDER BY p.id DESC",
      )
      .all(chosen ?? "")
      .map((r) => ({
        id: Number(r["id"]),
        session: str(r["session"]),
        slot: str(r["slot"]),
        module: str(r["module"]),
        kind: String(r["kind"]),
        status: String(r["status"]),
        rejectReason: str(r["reject_reason"]),
        experimentId: str(r["experiment_id"]),
        payload: parse<Record<string, unknown>>(r["payload_json"], {}),
        createdAt: str(r["created_at"]),
      }));

    const experiments: AdvisorExperiment[] = db
      .prepare<[], Record<string, unknown>>(
        "SELECT * FROM experiments ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'queued' THEN 1" +
          " ELSE 2 END, updated_at DESC",
      )
      .all()
      .map((r) => shapeExperiment(db, r));

    // The artifact the deep slot wrote is for the NEXT trading session, so that is the one to read
    // back. Rather than reimplementing the NYSE calendar here, take the session named by whatever
    // artifact is actually on disk — the producer already did the calendar walk.
    const nextSession = latestAdviceSession(config, chosen ?? null);

    return {
      sessions,
      session: chosen ?? null,
      latest,
      checkpoints: history,
      proposals,
      experiments,
      applyStatus: readApplyStatus(config, nextSession, readEnactment(db, chosen ?? null)),
      storePresent: true,
    };
  });
}

/**
 * The session the most recent advice artifact names, at or after `after`.
 *
 * Reading the filename rather than computing "the next trading day" keeps one calendar in the
 * suite — the Python one — and means a Friday artifact correctly reads as Monday's here without
 * this file knowing that NYSE closes on Thanksgiving.
 */
function latestAdviceSession(config: ConsoleConfig, after: string | null): string | null {
  let names: string[];
  try {
    names = fs.readdirSync(config.paths.adviceDir);
  } catch {
    return null;
  }
  const sessions = names
    .filter((n) => n.endsWith(".json"))
    .map((n) => n.replace(/\.json$/, "").split("-").slice(1).join("-"))
    .filter((s) => /^\d{4}-\d{2}-\d{2}$/.test(s) && (after === null || s >= after))
    .sort();
  return sessions[sessions.length - 1] ?? null;
}
