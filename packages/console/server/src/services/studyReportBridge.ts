import type {
  BacktestScorecard,
  BridgedResult,
  StudyAdvice,
  StudyAdviceKind,
  StudyDigest,
  StudyDigestRow,
  StudyGate,
  StudyMeasure,
  StudyMeasureKey,
  StudyParagraph,
  StudyReport,
  StudyReportArm,
  StudyReportVerdict,
  StudyStat,
  StudyTally,
} from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * One study read as a report, and the studies active since a date (`python -m cherrypick.cli
 * study-report --study <slug>` / `--since <date>`; the orchestrator's `studyreport`). Every figure,
 * reading and piece of advice is the orchestrator's: this renames the reply and decides nothing.
 *
 * Memoised ~30 s per argument, as the board is. The caller is NOT the gate: the route refuses before
 * reaching here unless the suite says `backtest: true` (the result files are licensed), and it checks
 * each argument's shape so nothing else is ever spawned.
 */

const UNAVAILABLE = "study report read unavailable — the orchestrator must be installed";
const MEASURES: readonly StudyMeasureKey[] = ["paired", "paired_both", "mean", "net_per_trade", "per_risk", "entry_rate"];
const ADVICE: readonly StudyAdviceKind[] = ["rerun", "data", "new_study", "ruleset", "reread", "register"];

export type StudyReportRequest = { study: string } | { since: string };

function argv(req: StudyReportRequest): string[] {
  return ["-m", "cherrypick.cli", "study-report", ...("study" in req ? ["--study", req.study] : ["--since", req.since])];
}

// The report reads every result file of a study, and the digest the whole board: allow a minute.
let caller: (req: StudyReportRequest) => Promise<SpawnedJson> = (req) => spawnModuleCliAsync(argv(req), UNAVAILABLE, [0, 1], 60_000);

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setStudyReportCaller(fn?: (req: StudyReportRequest) => Promise<SpawnedJson>): void {
  caller = fn ?? ((req) => spawnModuleCliAsync(argv(req), UNAVAILABLE, [0, 1], 60_000));
}

export const TTL_MS = 30_000;
const cache = new Map<string, { at: number; value: Promise<SpawnedJson> }>();

/** Drop the memoised responses. Tests only. */
export function resetStudyReportCache(): void {
  cache.clear();
}

function memo(req: StudyReportRequest, now: number): Promise<SpawnedJson> {
  const key = JSON.stringify(req);
  const hit = cache.get(key);
  if (hit && now - hit.at < TTL_MS) return hit.value;
  const value = caller(req);
  cache.set(key, { at: now, value });
  return value;
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function s(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function n(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function strings(v: unknown): string[] {
  return list(v).filter((x): x is string => typeof x === "string");
}

function toStat(v: unknown): StudyStat | null {
  const r = rec(v);
  const mean = n(r["mean"]);
  return mean === null ? null : { mean, t: n(r["t"]), n: n(r["n"]), p: n(r["p"]) };
}

/** An arm's `scorecard` block, shape-checked: stats a record, insights a list of {key, text, flag}. */
function toScorecard(v: unknown): BacktestScorecard | null {
  const r = rec(v);
  const stats = rec(r["stats"]);
  if (!Array.isArray(r["insights"]) || Object.keys(stats).length === 0) return null;
  const insights = (r["insights"] as unknown[]).flatMap((x) => {
    const i = rec(x);
    const key = s(i["key"]);
    const text = s(i["text"]);
    return key === null || text === null ? [] : [{ key, text, flag: i["flag"] === true }];
  });
  return { stats: stats as unknown as BacktestScorecard["stats"], insights };
}

function toArm(v: unknown): StudyReportArm | null {
  const r = rec(v);
  const arm = s(r["arm"]);
  const window = s(r["window"]);
  const mean = n(r["mean"]);
  if (arm === null || window === null || mean === null) return null;
  return {
    arm,
    group: s(r["group"]) ?? "",
    window,
    file: s(r["file"]) ?? "",
    mean,
    t: n(r["t"]),
    n: n(r["n"]),
    p: n(r["p"]),
    verdict: s(r["verdict"]),
    sessions: n(r["sessions"]),
    entryRate: n(r["entry_rate"]),
    netPerTrade: n(r["net_per_trade"]),
    netPerTradeDerived: r["net_per_trade_derived"] === true,
    perRisk: n(r["per_risk"]),
    perRiskBase: n(r["per_risk_base"]),
    paired: toStat(r["paired"]),
    pairedVs: s(r["paired_vs"]),
    pairedBoth: toStat(r["paired_both"]),
    scorecard: toScorecard(r["scorecard"]),
  };
}

function toMeasure(v: unknown): StudyMeasure | null {
  const r = rec(v);
  const key = s(r["key"]) as StudyMeasureKey | null;
  const label = s(r["label"]);
  if (key === null || !MEASURES.includes(key) || label === null) return null;
  const t = s(r["t"]) as StudyMeasureKey | null;
  return { key, label, note: s(r["note"]) ?? "", t: t !== null && MEASURES.includes(t) ? t : null };
}

function toParagraph(v: unknown): StudyParagraph | null {
  const r = rec(v);
  const text = s(r["text"]);
  return text === null ? null : { kind: s(r["kind"]) ?? "", text };
}

function toAdvice(v: unknown): StudyAdvice | null {
  const r = rec(v);
  const kind = s(r["kind"]) as StudyAdviceKind | null;
  const text = s(r["text"]);
  if (kind === null || !ADVICE.includes(kind) || text === null) return null;
  return { kind, text, why: s(r["why"]) ?? "" };
}

function toGate(v: unknown): StudyGate | null {
  const r = rec(v);
  const key = s(r["key"]);
  const name = s(r["name"]);
  const text = s(r["text"]);
  return key === null || name === null || text === null ? null : { key, name, text };
}

function toVerdict(v: unknown): StudyReportVerdict | null {
  const r = rec(v);
  const verdict = s(r["verdict"]);
  return verdict === null ? null : { arm: s(r["arm"]), verdict, why: s(r["why"]), file: s(r["file"]) ?? "" };
}

function nonNull<T>(x: T | null): x is T {
  return x !== null;
}

function failed(res: SpawnedJson): string | null {
  if (!res.ok || res.json === null) return res.error ?? UNAVAILABLE;
  if (res.json["ok"] !== true) return String(res.json["error"] ?? UNAVAILABLE);
  return null;
}

export function shapeReport(res: SpawnedJson): BridgedResult<StudyReport> {
  const error = failed(res);
  if (error !== null || res.json === null) return { ok: false, data: null, error: error ?? UNAVAILABLE };
  const j = res.json;
  return {
    ok: true,
    data: {
      study: s(j["study"]) ?? "",
      provisional: j["provisional"] === true,
      windows: strings(j["windows"]),
      arms: list(j["arms"]).map(toArm).filter(nonNull),
      comparisons: list(j["comparisons"]).map(toArm).filter(nonNull),
      verdicts: list(j["verdicts"]).map(toVerdict).filter(nonNull),
      measures: list(j["measures"]).map(toMeasure).filter(nonNull),
      gates: list(j["gates"]).map(toGate).filter(nonNull),
      reading: list(j["reading"]).map(toParagraph).filter(nonNull),
      advice: list(j["advice"]).map(toAdvice).filter(nonNull),
      failedSteps: strings(j["failed_steps"]),
    },
    error: null,
  };
}

function toTally(v: unknown): StudyTally {
  const r = rec(v);
  return { pass: n(r["pass"]) ?? 0, fail: n(r["fail"]) ?? 0, noVerdict: n(r["no_verdict"]) ?? 0 };
}

function toDigestRow(v: unknown): StudyDigestRow | null {
  const r = rec(v);
  const id = n(r["id"]);
  const study = s(r["study"]);
  const title = s(r["title"]);
  const finished = s(r["finished"]);
  if (id === null || study === null || title === null || finished === null) return null;
  return {
    id,
    study,
    title,
    status: s(r["status"]) ?? "",
    outcome: s(r["outcome"]),
    finished,
    tally: toTally(r["tally"]),
    provisional: r["provisional"] === true,
    failedSteps: strings(r["failed_steps"]),
  };
}

export function shapeDigest(res: SpawnedJson): BridgedResult<StudyDigest> {
  const error = failed(res);
  if (error !== null || res.json === null) return { ok: false, data: null, error: error ?? UNAVAILABLE };
  const j = res.json;
  return {
    ok: true,
    data: {
      since: s(j["since"]) ?? "",
      studies: list(j["studies"]).map(toDigestRow).filter(nonNull),
      totals: toTally(j["totals"]),
      noReading: list(j["no_reading"]).filter((x): x is number => typeof x === "number"),
    },
    error: null,
  };
}

/** The caller has checked `study` is a slug. */
export async function readStudyReport(study: string, now = Date.now()): Promise<BridgedResult<StudyReport>> {
  return shapeReport(await memo({ study }, now));
}

/** The caller has checked `since` is a date. */
export async function readStudyDigest(since: string, now = Date.now()): Promise<BridgedResult<StudyDigest>> {
  return shapeDigest(await memo({ since }, now));
}
