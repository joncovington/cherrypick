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
  AdvisorApplyStatus,
  AdvisorCheckpoint,
  AdvisorEvent,
  AdvisorExperiment,
  AdvisorFlag,
  AdvisorPayload,
  AdvisorProposal,
  AdvisorVerdict,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "./db.js";

/** The advisor's own slot order — chronological, not alphabetical. */
const SLOT_ORDER = ["am", "midday", "pm", "deep"];
const MODULES = ["meic", "flies", "earnings", "calendars"];
const HISTORY_LIMIT = 40;

function parse<T>(raw: unknown, fallback: T): T {
  if (typeof raw !== "string" || raw === "") return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.advisorDir, "advisor.db");
}

interface CheckpointRow {
  session: string;
  slot: string;
  model: string | null;
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
    ok: row.ok === 1,
    error: row.error,
    observations: parse<string[]>(row.observations_json, []),
    flags: parse<AdvisorFlag[]>(row.flags_json, []),
    createdAt: row.created_at,
  };
}

/**
 * What reached the loops for the next session, per module.
 *
 * Two facts, kept separate on purpose: the advisor WROTE an artifact, and the module's loop APPLIED
 * it. They come apart in ordinary operation — a module that stopped accepting advice, a loop that
 * has not started its session yet, a reject-all artifact that legitimately carries nothing — and
 * collapsing them into one "advice is on" badge would hide exactly the cases worth looking at.
 */
function readApplyStatus(config: ConsoleConfig, nextSession: string | null): AdvisorApplyStatus[] {
  return MODULES.map((module) => {
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
      consumerDecision: decision,
      disabledReason,
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
    applyStatus: readApplyStatus(config, null),
    storePresent: false,
  };

  return withReadOnlyDb(dbPath(config), empty, (db): AdvisorPayload => {
    const sessions = db
      .prepare<[], { session: string }>("SELECT DISTINCT session FROM checkpoints ORDER BY session")
      .all()
      .map((r) => r.session);
    const chosen = session !== undefined && sessions.includes(session) ? session : sessions[sessions.length - 1];

    const history = db
      .prepare<[number], CheckpointRow>(
        "SELECT session, slot, model, ok, error, observations_json, flags_json, created_at" +
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
      .map((r) => {
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
          hypothesis: str(r["hypothesis"]),
          successMetric: str(r["success_metric"]),
          params: parse<Record<string, unknown>>(r["params_json"], {}),
          status: String(r["status"]),
          createdSession: String(r["created_session"]),
          sessionsRun: Number(r["sessions_run"] ?? 0),
          expiresAfter: Number(r["expires_after_sessions"] ?? 0),
          verdict: parse<AdvisorVerdict | null>(r["verdict_json"], null),
          journal,
        };
      });

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
      applyStatus: readApplyStatus(config, nextSession),
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
