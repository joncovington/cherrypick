import type { TradeSummarySlice } from "@console/shared";
import fs from "node:fs";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * The Reports summary page's per-module slices, read from the module that owns the math.
 *
 * `python -m cherrypick.core.metrics summary` runs the exact normalisation every calibration
 * surface already runs — `ledgers.READERS` + `calibration_reading`'s own honesty rules — at
 * arbitrary window widths, which the review artifact (one fixed session) cannot. A TypeScript
 * re-derivation would be the exact mistake `services/report.ts` already made once (its own
 * docstring calls the net rules "copied exactly", and they had already drifted from the
 * orchestrator's). Same bridging pattern and reason as `metricsBridge.ts`, which this extends
 * rather than duplicates: the same subprocess, the same "unknown is not free capital" rule, the
 * same snake_case-inside-reading convention.
 *
 * Field names stay snake_case, matching the CLI's own JSON verbatim (the
 * `ScreenMetrics`/`funnel` convention this package already uses); the reader composes them into
 * the shared `TradeSummarySlice` (camelCase), which is presentation shaping, not derivation.
 */

export interface SummaryHero {
  trades: number;
  sessions: number;
  net: number;
  gross: number;
  fees: number;
  wins: number;
  win_pct: number | null;
  capital: number | null;
  capital_coverage: string;
}

export interface SummarySlice {
  ok: boolean;
  schema: string;
  window: { from: string | null; to: string | null };
  hero: SummaryHero;
  return: { value: number | null; coverage: string };
  by_arm: Record<
    string,
    { trades: number; sessions: number; net: number; gross: number; fees: number; wins: number; win_pct: number | null; capital_coverage: string; return: number | null }
  >;
  daily: Array<{ session: string; net: number; trades: number; wins: number }>;
  facets: { sessions: string[]; symbols: string[]; arms: string[] };
  capture: { value: number | null; n: number };
  error?: string;
}

export interface SummaryArgs {
  dbPath: string;
  schema: string;
  session: string | null;
  symbol: string | null;
  arm: string | null;
  width: string;
  start: string | null;
  end: string | null;
  /** pmcc alone stamps rows with an era (`ledgers.ERA_SCHEMAS`); any other schema is refused by
   *  the CLI rather than silently unfiltered, so only pmcc's reader passes this. */
  era: string | null;
}

const UNAVAILABLE =
  "summary unavailable — cherrypick-core must be installed (pip install -e packages/core)";

function spawnCaller(a: SummaryArgs): SummarySlice {
  // An ABSENT store is its own refusal, distinct from "the package is not installed": the
  // console's own readers answer `readOnlyDb`'s {status: "absent"} for it, and the page must
  // say "no store — the module never ran here", not accuse the install. Probed before the
  // spawn, because spawnModuleCli cannot tell one from the other (the CLI's error is a JSON
  // stdout with exit 0 either way).
  if (!fs.existsSync(a.dbPath)) {
    return errorSlice(a, `no store — ${a.dbPath} does not exist (the module never ran here)`);
  }
  const argv = ["-m", "cherrypick.core.metrics", "summary", "--db", a.dbPath, "--schema", a.schema];
  if (a.session !== null) argv.push("--session", a.session);
  if (a.symbol !== null) argv.push("--symbol", a.symbol);
  if (a.arm !== null) argv.push("--arm", a.arm);
  argv.push("--width", a.width);
  // The suite epoch's bound, for the schemas that carry no era stamp (`ledgers.ERA_SCHEMAS` is
  // pmcc alone); the CLI takes them exactly as `read` does. A --session outside the bound is
  // not the CLI's business: the bounds already removed it.
  if (a.start !== null) argv.push("--start", a.start);
  if (a.end !== null) argv.push("--end", a.end);
  // pmcc's own stamped-era scope (the module's era-scoping rule, `readers/pmcc.ts` CURRENT_ERA),
  // scoped via the same --era flag `read` takes — refused by the CLI for any other schema.
  if (a.era !== null) argv.push("--era", a.era);
  const res = spawnModuleCli(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) {
    return errorSlice(a, res.error ?? UNAVAILABLE);
  }
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return errorSlice(a, typeof err === "string" ? err : `${UNAVAILABLE} — refused`);
  }
  return res.json as unknown as SummarySlice;
}

/** One failed module's slice: every key present, nothing zero, the error riding at the top.
 *  One absent db degrades ITS slice (an unreadable card head) and never the page. */
function errorSlice(a: SummaryArgs, error: string): SummarySlice {
  return {
    ok: false,
    schema: a.schema,
    window: { from: null, to: null },
    hero: { trades: 0, sessions: 0, net: 0, gross: 0, fees: 0, wins: 0, win_pct: null, capital: null, capital_coverage: "0/0" },
    return: { value: null, coverage: "0/0" },
    by_arm: {},
    daily: [],
    facets: { sessions: [], symbols: [], arms: [] },
    capture: { value: null, n: 0 },
    error,
  };
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setSummaryCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
}

// A summary replays a whole ledger through the normalisation, and the answer moves only when a
// new trade closes. The page polls; this must not turn a poll into one subprocess per module.
// Same reasoning and TTL as metricsBridge.ts — the two caches key the same way, so a test that
// resets one can see the other's answer only by its own key.
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: SummarySlice }>();

export function readModuleSummary(a: SummaryArgs, now = Date.now()): SummarySlice {
  const key = `${a.dbPath}|${a.schema}|${a.session ?? ""}|${a.symbol ?? ""}|${a.arm ?? ""}|${a.width}|${a.start ?? ""}|${a.end ?? ""}|${a.era ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(a);
  cache.set(key, { at: now, value });
  return value;
}

/** Drop the memoised summaries. Tests only. */
export function resetSummaryCache(): void {
  cache.clear();
}
