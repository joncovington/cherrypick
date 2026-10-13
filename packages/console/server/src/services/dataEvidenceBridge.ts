import path from "node:path";
import type { BridgedResult, ImpliedVarCheck, ImpliedVarEvidence, QuoteHistoryCoverage } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { spawnModuleCli } from "./moduleCli.js";

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

type Spawned = ReturnType<typeof spawnModuleCli>;
type Request = { kind: "coverage" } | { kind: "impliedvar"; index: string };

function spawnRequest(config: ConsoleConfig, req: Request): Spawned {
  if (req.kind === "coverage") {
    const db = path.join(config.paths.gexDir, "gex_history.db");
    return spawnModuleCli(["-m", "cherrypick.core.quotehistory", "coverage", "--db", db], QH_UNAVAILABLE);
  }
  return spawnModuleCli(
    ["-m", "cherrypick.core.impliedvar", "check", "--index", req.index, "--db", config.paths.streamCacheDb],
    IV_UNAVAILABLE,
    [0, 1, 2],
  );
}

let caller = spawnRequest;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setDataEvidenceCaller(fn?: typeof spawnRequest): void {
  caller = fn ?? spawnRequest;
}

const COVERAGE_TTL_MS = 300_000;
const CHECK_TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: Spawned }>();

/** Drop the memoised responses. Tests only. */
export function resetDataEvidenceCache(): void {
  cache.clear();
}

function memo(config: ConsoleConfig, req: Request, ttl: number, now: number): Spawned {
  const key = `${req.kind}|${req.kind === "impliedvar" ? req.index : ""}|${config.paths.gexDir}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < ttl) return hit.value;
  const value = caller(config, req);
  cache.set(key, { at: now, value });
  return value;
}

export function readQuoteHistoryCoverage(config: ConsoleConfig, now = Date.now()): BridgedResult<QuoteHistoryCoverage> {
  const res = memo(config, { kind: "coverage" }, COVERAGE_TTL_MS, now);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? QH_UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? QH_UNAVAILABLE) };
  return { ok: true, data: res.json as unknown as QuoteHistoryCoverage, error: null };
}

export function readImpliedVarChecks(config: ConsoleConfig, now = Date.now()): ImpliedVarEvidence {
  const checks: Record<string, BridgedResult<ImpliedVarCheck>> = {};
  for (const index of IMPLIEDVAR_INDICES) {
    const res = memo(config, { kind: "impliedvar", index }, CHECK_TTL_MS, now);
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
