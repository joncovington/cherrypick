import path from "node:path";
import type { BridgedResult, ImpliedVarCheck, ImpliedVarEvidence, QuoteHistoryCoverage } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * System › data's two reads of `cherrypick.core`:
 *
 * - **Quote-history coverage** (`python -m cherrypick.core.quotehistory coverage`): per session,
 *   whether the gex recorder kept the chain's quotes, how many samples, first and last, the gaps
 *   inside the session and the samples whose spot could not be joined. It reads ONLY through
 *   `core.quotehistory.session()` -- never the table, whose layout is the recorder's to change --
 *   so this console holds no SQL against `gex_quote_history` at all.
 * - **The implied-variance check** (`python -m cherrypick.core.impliedvar check --index VIX|VIX9D`):
 *   the module's own model-free arithmetic against the published index, now. `not_bracketed` (no
 *   pair of cached expirations around the target) is the cache's normal state and is shown as that
 *   reason, never as a failure; `truncated` is a strip the cache's window cut, which reads low by
 *   construction and so is no verdict. The verb exits 1 on anything but agreement and 2 when the
 *   cache cannot be read, with its reading on stdout either way.
 *
 * Memoised: coverage moves once a session, and the check is a snapshot that only means something
 * while the cache is live.
 */

const QH_UNAVAILABLE = "quote-history coverage unavailable — cherrypick-core must be installed";
const IV_UNAVAILABLE = "implied-variance check unavailable — cherrypick-core must be installed";

/** The indices `core.impliedvar` reproduces (its own `INDEX_DAYS`). */
export const IMPLIEDVAR_INDICES = ["VIX9D", "VIX"] as const;

type Request = { kind: "coverage" } | { kind: "impliedvar"; index: string };

function spawnRequest(config: ConsoleConfig, req: Request): Promise<SpawnedJson> {
  if (req.kind === "coverage") {
    const db = path.join(config.paths.gexDir, "gex_history.db");
    return spawnModuleCliAsync(["-m", "cherrypick.core.quotehistory", "coverage", "--db", db], QH_UNAVAILABLE);
  }
  return spawnModuleCliAsync(
    ["-m", "cherrypick.core.impliedvar", "check", "--index", req.index, "--db", config.paths.streamCacheDb],
    IV_UNAVAILABLE,
    [0, 1, 2],
  );
}

type Caller = (config: ConsoleConfig, req: Request) => SpawnedJson | Promise<SpawnedJson>;
let caller: Caller = spawnRequest;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setDataEvidenceCaller(fn?: Caller): void {
  caller = fn ?? spawnRequest;
}

const COVERAGE_TTL_MS = 300_000;
const CHECK_TTL_MS = 120_000;
const cache = new AsyncMemo<SpawnedJson>();

/** Drop the memoised responses. Tests only. */
export function resetDataEvidenceCache(): void {
  cache.clear();
}

function memo(config: ConsoleConfig, req: Request, ttl: number, now: number): Promise<SpawnedJson> {
  const key = `${req.kind}|${req.kind === "impliedvar" ? req.index : ""}|${config.paths.gexDir}`;
  return cache.get(key, ttl, now, () => caller(config, req));
}

export async function readQuoteHistoryCoverage(
  config: ConsoleConfig,
  now = Date.now(),
): Promise<BridgedResult<QuoteHistoryCoverage>> {
  const res = await memo(config, { kind: "coverage" }, COVERAGE_TTL_MS, now);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? QH_UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? QH_UNAVAILABLE) };
  return { ok: true, data: res.json as unknown as QuoteHistoryCoverage, error: null };
}

export async function readImpliedVarChecks(config: ConsoleConfig, now = Date.now()): Promise<ImpliedVarEvidence> {
  const checks: Record<string, BridgedResult<ImpliedVarCheck>> = {};
  const reads = await Promise.all(IMPLIEDVAR_INDICES.map((index) => memo(config, { kind: "impliedvar", index }, CHECK_TTL_MS, now)));
  for (const [i, index] of IMPLIEDVAR_INDICES.entries()) {
    const res = reads[i]!;
    if (!res.ok || res.json === null) {
      checks[index] = { ok: false, data: null, error: res.error ?? IV_UNAVAILABLE };
    } else if (typeof res.json["error"] === "string") {
      // Exit 2: the cache could not be read at all -- a failure, not a reading.
      checks[index] = { ok: false, data: null, error: res.json["error"] };
    } else {
      // A refusal (`ok: false` with a `reason`) IS the reading; it is passed on as data.
      checks[index] = { ok: true, data: res.json as unknown as ImpliedVarCheck, error: null };
    }
  }
  return { checks };
}
