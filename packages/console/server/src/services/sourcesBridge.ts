import type { BridgedResult, SourceCoverage, SourceRow } from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * Backtest → sources reads the source registry (`cherrypick.core.sources`, #1194) through its own
 * command line, so what a source is and how its span is read stay core's:
 *
 * - `list`: every declared source, with its state and span from the last inventory.
 * - `coverage --field F --symbol S --from D --to D [...]`: which source covers that request, or why
 *   none does. Exit 2 with the reason on stderr for a value core does not know.
 *
 * Both read only the inventory's cache file (`state/sources_inventory.json`); neither opens a store
 * or contacts anyone. Memoised ~30 s.
 *
 * **This module is not the licence gate.** A vendor source may only be listed while its results may
 * be shown, and `routes/sources.ts` decides that.
 */

const UNAVAILABLE = "source registry read unavailable — cherrypick-core must be installed";

export interface CoverageRequest {
  field: string;
  symbol: string;
  start: string;
  end: string;
  cadence?: string;
  expiry?: string;
  licensed: boolean;
}

export type SourcesRequestKind = { kind: "list" } | { kind: "coverage"; req: CoverageRequest };

export function coverageArgv(req: CoverageRequest): string[] {
  const argv = ["-m", "cherrypick.core.sources", "coverage", "--field", req.field, "--symbol", req.symbol, "--from", req.start, "--to", req.end];
  if (req.cadence !== undefined) argv.push("--cadence", req.cadence);
  if (req.expiry !== undefined) argv.push("--expiry", req.expiry);
  if (req.licensed) argv.push("--licensed");
  return argv;
}

function spawnRequest(r: SourcesRequestKind): Promise<SpawnedJson> {
  if (r.kind === "list") return spawnModuleCliAsync(["-m", "cherrypick.core.sources", "list"], UNAVAILABLE);
  return spawnModuleCliAsync(coverageArgv(r.req), UNAVAILABLE);
}

let caller: (r: SourcesRequestKind) => Promise<SpawnedJson> = spawnRequest;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setSourcesCaller(fn?: (r: SourcesRequestKind) => Promise<SpawnedJson>): void {
  caller = fn ?? spawnRequest;
}

export const TTL_MS = 30_000;
const cache = new Map<string, { at: number; value: Promise<SpawnedJson> }>();

/** Drop the memoised responses. Tests only. */
export function resetSourcesCache(): void {
  cache.clear();
}

function memo(r: SourcesRequestKind, now: number): Promise<SpawnedJson> {
  const key = JSON.stringify(r);
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  for (const [k, v] of cache) if (now - v.at >= TTL_MS) cache.delete(k);
  const value = caller(r);
  cache.set(key, { at: now, value });
  return value;
}

/** Every declared source, before the route withholds any licensed one. */
export async function readSources(
  now = Date.now(),
): Promise<BridgedResult<{ generated_at: string | null; sources: SourceRow[]; undeclared: string[] }>> {
  const res = await memo({ kind: "list" }, now);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  const sources = res.json["sources"];
  const undeclared = res.json["undeclared"];
  if (!Array.isArray(sources)) return { ok: false, data: null, error: `${UNAVAILABLE} — malformed reply` };
  const generated = res.json["generated_at"];
  return {
    ok: true,
    data: {
      generated_at: typeof generated === "string" ? generated : null,
      sources: sources as SourceRow[],
      undeclared: Array.isArray(undeclared) ? (undeclared as string[]) : [],
    },
    error: null,
  };
}

/** One coverage answer. The route has already checked every value against core's lists. */
export async function readCoverage(req: CoverageRequest, now = Date.now()): Promise<BridgedResult<SourceCoverage>> {
  const res = await memo({ kind: "coverage", req }, now);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (!Array.isArray(res.json["sources"]) || typeof res.json["covered"] !== "boolean") {
    return { ok: false, data: null, error: `${UNAVAILABLE} — malformed reply` };
  }
  return { ok: true, data: res.json as unknown as SourceCoverage, error: null };
}
