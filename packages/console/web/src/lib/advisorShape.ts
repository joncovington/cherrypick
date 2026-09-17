import type {
  AdvisorActiveExperiment,
  AdvisorApplyStatus,
  AdvisorEnactment,
  AdvisorExperiment,
  AdvisorModulePayload,
  AdvisorPayload,
} from "@console/shared";

/**
 * Bring an advisor payload from the server up to the shape the page reads, whatever build wrote it.
 *
 * The supervisor launches `server/dist` and restarts it on its own schedule, while the SPA is
 * served straight off `web/dist` — so every `pnpm build` opens a window where a NEW page reads an
 * OLD API. The 2026-09-17 change (several experiments per module, each its own book) turned three
 * single values into lists: `active`, `enactment`, and the artifact/decision experiment entries.
 * A page that assumed the lists went blank on the live console the moment it was built, before
 * any restart. Normalising once at the fetch boundary keeps that window harmless; nothing below
 * this file needs to know both shapes exist.
 *
 * Only shapes, never judgements: the values are the advisor's own, passed through.
 */

type Loose = Record<string, unknown>;

function asList<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function experimentTag(e: Loose): string | null {
  if (typeof e["tag"] === "string") return e["tag"];
  return null;
}

function normalizeExperiment(e: Loose): AdvisorExperiment {
  return { ...(e as unknown as AdvisorExperiment), tag: experimentTag(e) };
}

export function normalizeApplyStatus(raw: unknown): AdvisorApplyStatus {
  const s = (raw ?? {}) as Loose;
  const proposals = asList<AdvisorApplyStatus["artifactProposals"][number]>(s["artifactProposals"]);
  const rejected = asList<AdvisorApplyStatus["artifactRejected"][number]>(s["artifactRejected"]);
  const legacyEnactment = (s["enactment"] ?? null) as AdvisorEnactment | null;
  const enactments = Array.isArray(s["enactments"])
    ? (s["enactments"] as AdvisorEnactment[])
    : legacyEnactment === null
      ? []
      : [legacyEnactment];
  const decision = (s["consumerDecision"] ?? null) as Loose | null;
  const artifactExperiments = Array.isArray(s["artifactExperiments"])
    ? (s["artifactExperiments"] as AdvisorApplyStatus["artifactExperiments"])
    : s["artifactWritten"] === true
      ? [{ experimentId: legacyEnactment?.experimentId ?? null, name: null, tag: null, base: null, proposals, rejected }]
      : [];
  const decisionExperiments = Array.isArray(s["decisionExperiments"])
    ? (s["decisionExperiments"] as AdvisorApplyStatus["decisionExperiments"])
    : decision === null
      ? []
      : [
          {
            experimentId: typeof decision["experiment_id"] === "string" ? decision["experiment_id"] : null,
            name: null,
            tag: null,
            base: null,
            params: (typeof decision["params"] === "object" ? decision["params"] : null) as Loose | null,
            reason: typeof decision["reason"] === "string" ? decision["reason"] : null,
          },
        ];
  return {
    module: String(s["module"] ?? ""),
    nextSession: (s["nextSession"] ?? null) as string | null,
    artifactWritten: s["artifactWritten"] === true,
    artifactProposals: proposals,
    artifactRejected: rejected,
    artifactExperiments,
    consumerDecision: decision,
    decisionExperiments,
    disabledReason: (s["disabledReason"] ?? null) as string | null,
    enactments,
  };
}

export function normalizeAdvisorPayload(raw: unknown): AdvisorPayload {
  const p = (raw ?? {}) as Loose;
  return {
    ...(p as unknown as AdvisorPayload),
    experiments: asList<Loose>(p["experiments"]).map(normalizeExperiment),
    applyStatus: asList<unknown>(p["applyStatus"]).map(normalizeApplyStatus),
  };
}

export function normalizeModulePayload(raw: unknown): AdvisorModulePayload {
  const p = (raw ?? {}) as Loose;
  const rawActive = p["active"];
  // The pre-2026-09-17 shape: one experiment (or null) with the module-level calendar age and
  // stall budget beside it.
  const activeList: Loose[] = Array.isArray(rawActive)
    ? (rawActive as Loose[])
    : rawActive !== null && typeof rawActive === "object"
      ? [rawActive as Loose]
      : [];
  const active: AdvisorActiveExperiment[] = activeList.map((e) => {
    const base = normalizeExperiment(e);
    const calendar = typeof e["calendarSessions"] === "number" ? e["calendarSessions"] : (p["calendarSessions"] as number | null | undefined);
    const budget = typeof e["stallBudget"] === "number" ? e["stallBudget"] : (p["stallBudget"] as number | null | undefined);
    return {
      ...base,
      calendarSessions: typeof calendar === "number" ? calendar : null,
      stallBudget: typeof budget === "number" ? budget : 2 * base.expiresAfter,
    };
  });
  return {
    module: String(p["module"] ?? ""),
    storePresent: p["storePresent"] === true,
    active,
    queued: asList<Loose>(p["queued"]).map(normalizeExperiment),
    concluded: asList<Loose>(p["concluded"]).map(normalizeExperiment),
    sessions: asList<AdvisorModulePayload["sessions"][number]>(p["sessions"]),
    tomorrow: p["tomorrow"] === null || p["tomorrow"] === undefined ? null : normalizeApplyStatus(p["tomorrow"]),
  };
}
