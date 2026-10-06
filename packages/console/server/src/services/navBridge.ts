import type {
  ContangoArmMetrics,
  ContangoMetrics,
  CurveArmEquity,
  CurveEquity,
  DatedValue,
  EquityReading,
  NavReading,
} from "@console/shared";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * Daily-series readings from the modules' own analytics (`contango metrics`, `curve equity`), both
 * computed by `cherrypick.core.metrics.nav` -- the console never re-derives a ratio it can read.
 * The Python side writes snake_case; this is the one place it becomes the console's camelCase, so a
 * renamed field fails here rather than rendering as a silent blank.
 */

const CONTANGO_UNAVAILABLE = "contango metrics unavailable — is cherrypick-contango installed?";
const CURVE_UNAVAILABLE = "curve equity unavailable — is cherrypick-curve installed?";

function camel(key: string): string {
  return key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

/** Rename one level of keys; `monthly` keys are dates, never renamed. */
function reading<T>(raw: unknown): T {
  const obj = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) out[camel(k)] = v;
  return out as T;
}

function series(raw: unknown): DatedValue[] {
  return Array.isArray(raw)
    ? raw.filter((p): p is [string, number] => Array.isArray(p) && typeof p[0] === "string" && typeof p[1] === "number")
    : [];
}

export function normalizeContango(json: Record<string, unknown>): Omit<ContangoMetrics, "ok" | "error"> {
  const arms: Record<string, ContangoArmMetrics> = {};
  for (const [arm, raw] of Object.entries((json["arms"] ?? {}) as Record<string, Record<string, unknown>>)) {
    arms[arm] = {
      startingCapital: typeof raw["starting_capital"] === "number" ? raw["starting_capital"] : null,
      reading: reading<NavReading>(raw["reading"]),
      series: series(raw["series"]),
      expected: series(raw["expected"]),
      expectedReading: raw["expected_reading"] === undefined ? null : reading<NavReading>(raw["expected_reading"]),
      tracking: typeof raw["tracking"] === "number" ? raw["tracking"] : null,
    };
  }
  const benchmarks: ContangoMetrics["benchmarks"] = {};
  for (const [symbol, raw] of Object.entries((json["benchmarks"] ?? {}) as Record<string, Record<string, unknown>>)) {
    benchmarks[symbol] = { series: series(raw["series"]), reading: reading<NavReading>(raw["reading"]) };
  }
  return { arms, benchmarks };
}

export function normalizeCurve(json: Record<string, unknown>): Omit<CurveEquity, "ok" | "error"> {
  const arms: Record<string, CurveArmEquity> = {};
  for (const [arm, raw] of Object.entries((json["arms"] ?? {}) as Record<string, Record<string, unknown>>)) {
    arms[arm] = {
      series: series(raw["series"]),
      carried: typeof raw["carried"] === "number" ? raw["carried"] : 0,
      reading: reading<EquityReading>(raw["reading"]),
    };
  }
  return { arms };
}

function contangoCaller(dbPath: string): ContangoMetrics {
  const res = spawnModuleCli(["-m", "cherrypick.contango.cli", "--db", dbPath, "metrics"], CONTANGO_UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error, arms: {}, benchmarks: {} };
  try {
    return { ok: true, error: null, ...normalizeContango(res.json) };
  } catch (err) {
    return { ok: false, error: `${CONTANGO_UNAVAILABLE} — ${(err as Error).message}`, arms: {}, benchmarks: {} };
  }
}

function curveCaller(dbPath: string): CurveEquity {
  const res = spawnModuleCli(["-m", "cherrypick.curve.cli", "--db", dbPath, "equity"], CURVE_UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error, arms: {} };
  try {
    return { ok: true, error: null, ...normalizeCurve(res.json) };
  } catch (err) {
    return { ok: false, error: `${CURVE_UNAVAILABLE} — ${(err as Error).message}`, arms: {} };
  }
}

let callers = { contango: contangoCaller, curve: curveCaller };

/** Swap the subprocesses out in tests. Pass nothing to restore the real ones. */
export function setNavCallers(fns?: Partial<typeof callers>): void {
  callers = { contango: fns?.contango ?? contangoCaller, curve: fns?.curve ?? curveCaller };
}

export function readContangoMetrics(dbPath: string): ContangoMetrics {
  return callers.contango(dbPath);
}

export function readCurveEquity(dbPath: string): CurveEquity {
  return callers.curve(dbPath);
}
