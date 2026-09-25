import type { AdvisorExperiment } from "@console/shared";

/** The last scored session's status, from the experiment's own journal. */
export function lastCounted(e: AdvisorExperiment): { session: string | null; status: string } | null {
  const counted = e.journal.filter((j) => j.event === "counted");
  const last = counted[counted.length - 1];
  if (last === undefined) return null;
  return { session: last.session, status: String(last.detail?.["status"] ?? "unknown") };
}

/** The first of `keys` that is PRESENT on `doc`, whatever it holds -- core.config.first_present. */
function firstPresent(doc: Record<string, unknown> | null | undefined, keys: readonly string[]): unknown {
  if (doc == null) return undefined;
  for (const key of keys) if (key in doc) return doc[key];
  return undefined;
}

// A reading's session count and the rule's bar for it were both spelled `days` until 2026-09-24
// (core.config.SESSIONS_KEYS / MIN_SESSIONS_KEYS). Verdicts and packs written before then say
// `days` and stay on disk, so both spellings are read, canonical first.
const SESSIONS_KEYS = ["sessions", "days"] as const;
const MIN_SESSIONS_KEYS = ["min_sessions", "min_days"] as const;

/** A calibration reading's distinct-session count, under either spelling. */
export function readingSessions(reading: Record<string, unknown> | null | undefined): number | null {
  const v = firstPresent(reading, SESSIONS_KEYS);
  return typeof v === "number" ? v : null;
}

/** "trades 9 of 20 · sessions 2 of 14" from a verdict pair's rule and its advised reading. */
export function gateDistance(e: AdvisorExperiment): string | null {
  const pair = e.verdict?.pairs[0];
  if (pair === undefined) return null;
  const rule = (pair as unknown as { rule?: Record<string, unknown> }).rule ?? {};
  const reading = pair.advised;
  const parts: string[] = [];
  const minSample = rule["min_sample"];
  const minSessions = firstPresent(rule, MIN_SESSIONS_KEYS);
  if (typeof minSample === "number") parts.push(`trades ${Number(reading?.["sample"] ?? 0)} of ${minSample}`);
  if (typeof minSessions === "number") parts.push(`sessions ${readingSessions(reading) ?? 0} of ${minSessions}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
