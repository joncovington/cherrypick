import type { AdvisorExperiment } from "@console/shared";

/** The last scored session's status, from the experiment's own journal. */
export function lastCounted(e: AdvisorExperiment): { session: string | null; status: string } | null {
  const counted = e.journal.filter((j) => j.event === "counted");
  const last = counted[counted.length - 1];
  if (last === undefined) return null;
  return { session: last.session, status: String(last.detail?.["status"] ?? "unknown") };
}

/** "trades 9 of 20 · days 2 of 14" from a verdict pair's rule and its advised reading. */
export function gateDistance(e: AdvisorExperiment): string | null {
  const pair = e.verdict?.pairs[0];
  if (pair === undefined) return null;
  const rule = (pair as unknown as { rule?: Record<string, unknown> }).rule ?? {};
  const reading = pair.advised;
  const parts: string[] = [];
  const minSample = rule["min_sample"];
  const minDays = rule["min_days"];
  if (typeof minSample === "number") parts.push(`trades ${Number(reading?.["sample"] ?? 0)} of ${minSample}`);
  if (typeof minDays === "number") parts.push(`days ${Number(reading?.["days"] ?? 0)} of ${minDays}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
