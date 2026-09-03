import { spawnModuleCli } from "./moduleCli.js";
import type {
  PerformanceModuleId,
  ExcursionPosition,
  ExcursionsDistribution,
  ExcursionsData,
  ExcursionsResult,
} from "@console/shared";

/**
 * MAE/MFE (docs/metrics-plan.md Phase 2) per closed position, via each module's own `excursions`
 * verb -- curve/calendars/pmcc (`<module>.cli --db PATH excursions`) and earnings (a different
 * shape: `earnings.db_paper get_excursions`, no `--db` flag at all, the same default-home
 * resolution `screenBridge.ts` already relies on for this module). meic/flies/bwb have no Python
 * excursions support: meic/flies were never wired (metrics-plan.md's own scope -- no general
 * per-position mark path for either), and bwb was deliberately left out of the Phase 3a rollout
 * (its reversal add-on changes a position's leg set mid-life without updating `entry_credit`, so
 * a naive P&L reconstruction would misattribute the add-on's own credit as a mark move -- see
 * `packages/curve/analytics.py`'s own bwb note in the console-refactor plan).
 *
 * Position shape genuinely differs per module (curve/calendars/pmcc tag by `book`, earnings by
 * `strategy`; earnings carries no per-position `n` since it mirrors two stored columns rather
 * than deriving MAE/MFE from a tick series) -- normalised here into one shape so
 * `ExcursionsCard.tsx` reads one contract regardless of which module fed it.
 */


const UNAVAILABLE = "excursions unavailable — this module has no per-position mark path this console reads (see services/excursionsBridge.ts)";

interface Spec {
  argv: (dbPath: string) => string[];
  /** Pull `{positions, mae_distribution, mfe_distribution}` out of the CLI's own JSON, and the raw
   * position rows into the normalised shape -- the one place each module's own field names
   * (`book` vs `strategy`, `position_id` vs `order_id`, an `n` per position or none) are read. */
  normalize: (json: Record<string, unknown>) => ExcursionsData;
}

function distribution(raw: unknown): ExcursionsDistribution {
  const obj = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const median = typeof obj["median"] === "number" ? (obj["median"] as number) : null;
  const n = typeof obj["n"] === "number" ? (obj["n"] as number) : 0;
  return { median, n };
}

function ledgerModuleNormalize(idKey: string, tagKey: string): Spec["normalize"] {
  return (json) => {
    const excursions = (json["excursions"] ?? {}) as Record<string, unknown>;
    const rows = Array.isArray(excursions["positions"]) ? excursions["positions"] : [];
    const positions: ExcursionPosition[] = rows.map((r: Record<string, unknown>) => ({
      id: String(r[idKey] ?? ""),
      tag: String(r[tagKey] ?? ""),
      symbol: typeof r["symbol"] === "string" ? r["symbol"] : "",
      mae: typeof r["mae"] === "number" ? r["mae"] : 0,
      mfe: typeof r["mfe"] === "number" ? r["mfe"] : 0,
      n: typeof r["n"] === "number" ? r["n"] : null,
    }));
    return {
      positions,
      maeDistribution: distribution(excursions["mae_distribution"]),
      mfeDistribution: distribution(excursions["mfe_distribution"]),
    };
  };
}

const SPECS: Partial<Record<PerformanceModuleId, Spec>> = {
  curve: {
    argv: (dbPath) => ["-m", "cherrypick.curve.cli", "--db", dbPath, "excursions"],
    normalize: ledgerModuleNormalize("position_id", "book"),
  },
  calendars: {
    argv: (dbPath) => ["-m", "cherrypick.calendars.cli", "--db", dbPath, "excursions"],
    normalize: ledgerModuleNormalize("position_id", "book"),
  },
  pmcc: {
    argv: (dbPath) => ["-m", "cherrypick.pmcc.cli", "--db", dbPath, "excursions"],
    normalize: ledgerModuleNormalize("position_id", "book"),
  },
  earnings: {
    // No --db flag on this module's CLI -- it resolves the paper ledger from the default home the
    // same way screenBridge.ts already relies on for earnings' screen_report verb.
    argv: () => ["-m", "cherrypick.earnings.db_paper", "get_excursions"],
    normalize: (json) => {
      const rows = Array.isArray(json["positions"]) ? json["positions"] : [];
      const positions: ExcursionPosition[] = rows.map((r: Record<string, unknown>) => ({
        id: String(r["order_id"] ?? ""),
        tag: typeof r["strategy"] === "string" ? r["strategy"] : "",
        symbol: typeof r["symbol"] === "string" ? r["symbol"] : "",
        mae: typeof r["mae"] === "number" ? r["mae"] : 0,
        mfe: typeof r["mfe"] === "number" ? r["mfe"] : 0,
        n: null, // earnings mirrors two stored columns, not a derived tick series -- no count to carry
      }));
      return {
        positions,
        maeDistribution: distribution(json["mae_distribution"]),
        mfeDistribution: distribution(json["mfe_distribution"]),
      };
    },
  },
};

function spawnCaller(module: PerformanceModuleId, dbPath: string): ExcursionsResult {
  const spec = SPECS[module];
  if (spec === undefined) return { ok: false, data: null, error: UNAVAILABLE };
  const res = spawnModuleCli(spec.argv(dbPath), UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, data: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  try {
    return { ok: true, data: spec.normalize(res.json), error: null };
  } catch (err) {
    return { ok: false, data: null, error: `${UNAVAILABLE} — unparseable response (${(err as Error).message})` };
  }
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setExcursionsCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
}

// MAE/MFE replays every usable mark for every closed position -- the same cost profile as a
// calibration reading, and the same reason for a long TTL: the answer only moves when a new
// position closes.
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: ExcursionsResult }>();

export function readExcursions(
  module: PerformanceModuleId,
  dbPath: string,
  now = Date.now(),
): ExcursionsResult {
  const key = `${module}|${dbPath}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(module, dbPath);
  cache.set(key, { at: now, value });
  return value;
}

/** Drop the memoised readings. Tests only. */
export function resetExcursionsCache(): void {
  cache.clear();
}
