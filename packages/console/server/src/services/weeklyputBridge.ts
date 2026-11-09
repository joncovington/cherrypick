import type {
  WeeklyputArmSummary,
  WeeklyputCycle,
  WeeklyputCycleName,
  WeeklyputCycles,
  WeeklyputStatus,
  WeeklyputSummary,
} from "@console/shared";
import { spawnModuleCliAsync } from "./moduleCli.js";

/**
 * The three weeklyput answers this package must not compute for itself, asked of the module's own
 * read-only CLI (`python -m cherrypick.weeklyput.cli`):
 *
 * - `status`: the calendar -- whether today is an entry session, the window, the next cycle. The
 *   rule for which Fridays are weekly expiries (never a standard monthly, never one whose 7-day
 *   trade shares a session with a monthly's) is the module's `clock.py`, checked against item 57's
 *   own calendar; a copy here would be free to drift.
 * - `cycles` and `summary`: the per-cycle equal-weight mean of net in bp, item 57's measure. A name
 *   missed in a cycle is absent from its mean, never a zero; that rule is `analytics.py`'s.
 *
 * `readers/weeklyput.ts` reads the ledger directly for everything else, as every other reader does.
 * Each failure folds to `{ ok: false, error }`, so the page says the module's read is unavailable
 * rather than showing an empty cycle table that looks like "no cycles yet".
 */

const UNAVAILABLE = "weeklyput read unavailable — is cherrypick-weeklyput installed?";

type Json = Record<string, unknown>;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const obj = (v: unknown): Json => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Json) : {});
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export function normaliseStatus(j: Json): Omit<WeeklyputStatus, "ok" | "error"> {
  return {
    date: str(j["date"]),
    decisionWindow: str(j["decision_window"]),
    entryToday: str(j["entry_today"]),
    nextCycle: str(j["next_cycle"]),
    nextEntrySession: str(j["next_entry_session"]),
    arms: strings(j["arms"]),
    names: strings(j["names"]),
    awaitingClose: strings(j["awaiting_close"]),
  };
}

function cycleName(raw: Json): WeeklyputCycleName {
  return {
    status: str(raw["status"]) ?? "",
    strike: num(raw["strike"]) ?? 0,
    entryDelta: num(raw["entry_delta"]),
    entryValue: num(raw["entry_value"]) ?? 0,
    exitValue: num(raw["exit_value"]),
    grossPnl: num(raw["gross_pnl"]),
    fees: num(raw["fees"]),
    settlement: num(raw["settlement"]),
    slippage: num(raw["slippage"]),
    netPnl: num(raw["net_pnl"]),
    netBp: num(raw["net_bp"]),
    exitReason: str(raw["exit_reason"]),
    assignmentExposed: raw["assignment_exposed"] === true,
  };
}

export function normaliseCycles(j: Json): WeeklyputCycle[] {
  const rows = Array.isArray(j["cycles"]) ? (j["cycles"] as unknown[]) : [];
  return rows.map((r) => {
    const c = obj(r);
    return {
      arm: str(c["arm"]) ?? "",
      expiration: str(c["expiration"]) ?? "",
      entrySession: str(c["entry_session"]) ?? "",
      names: Object.fromEntries(Object.entries(obj(c["names"])).map(([sym, n]) => [sym, cycleName(obj(n))])),
      unentered: Object.fromEntries(
        Object.entries(obj(c["unentered"])).map(([sym, u]) => [
          sym,
          { outcome: str(obj(u)["outcome"]) ?? "", refusal: str(obj(u)["refusal"]) },
        ]),
      ),
      finished: c["finished"] === true,
      cycleNetBp: num(c["cycle_net_bp"]),
      netPnl: num(c["net_pnl"]),
    };
  });
}

function armSummary(a: Json): WeeklyputArmSummary {
  const worst = obj(a["worst"]);
  const money = obj(a["money"]);
  return {
    cycles: num(a["cycles"]) ?? 0,
    meanBp: num(a["mean_bp"]),
    stdevBp: num(a["stdev_bp"]),
    positiveShare: num(a["positive_share"]),
    worst: str(worst["expiration"]) === null || num(worst["cycle_net_bp"]) === null
      ? null
      : { expiration: str(worst["expiration"])!, cycleNetBp: num(worst["cycle_net_bp"])! },
    byName: Object.fromEntries(
      Object.entries(obj(a["by_name"])).map(([sym, n]) => [sym, { trades: num(obj(n)["trades"]) ?? 0, meanBp: num(obj(n)["mean_bp"]) ?? 0 }]),
    ),
    money: {
      entryValue: num(money["entry_value"]) ?? 0,
      exitValue: num(money["exit_value"]) ?? 0,
      fees: num(money["fees"]) ?? 0,
      settlement: num(money["settlement"]) ?? 0,
      slippage: num(money["slippage"]) ?? 0,
      grossPnl: num(money["gross_pnl"]) ?? 0,
      netPnl: num(money["net_pnl"]) ?? 0,
      feeDrag: num(money["fee_drag"]),
    },
    missed: num(a["missed"]) ?? 0,
  };
}

export function normaliseSummary(j: Json): Omit<WeeklyputSummary, "ok" | "error"> {
  return { arms: Object.fromEntries(Object.entries(obj(j["arms"])).map(([arm, a]) => [arm, armSummary(obj(a))])) };
}

type Verb = "status" | "cycles" | "summary";
type Caller = (dbPath: string, verb: Verb) => Promise<{ ok: boolean; json: Json | null; error: string | null }>;

const realCaller: Caller = (dbPath, verb) =>
  spawnModuleCliAsync(["-m", "cherrypick.weeklyput.cli", "--db", dbPath, verb], UNAVAILABLE);

let caller: Caller = realCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setWeeklyputCaller(fn?: Caller): void {
  caller = fn ?? realCaller;
}

async function ask<T extends { ok: boolean; error: string | null }>(
  dbPath: string,
  verb: Verb,
  shape: (j: Json) => Omit<T, "ok" | "error">,
  failed: Omit<T, "ok" | "error">,
): Promise<T> {
  const res = await caller(dbPath, verb);
  if (!res.ok || res.json === null) return { ok: false, error: res.error, ...failed } as T;
  try {
    return { ok: true, error: null, ...shape(res.json) } as T;
  } catch (err) {
    return { ok: false, error: `${UNAVAILABLE} — ${(err as Error).message}`, ...failed } as T;
  }
}

export function readWeeklyputStatus(dbPath: string): Promise<WeeklyputStatus> {
  return ask<WeeklyputStatus>(dbPath, "status", normaliseStatus, {
    date: null,
    decisionWindow: null,
    entryToday: null,
    nextCycle: null,
    nextEntrySession: null,
    arms: [],
    names: [],
    awaitingClose: [],
  });
}

export function readWeeklyputCycles(dbPath: string): Promise<WeeklyputCycles> {
  return ask<WeeklyputCycles>(dbPath, "cycles", (j) => ({ cycles: normaliseCycles(j) }), { cycles: [] });
}

export function readWeeklyputSummary(dbPath: string): Promise<WeeklyputSummary> {
  return ask<WeeklyputSummary>(dbPath, "summary", normaliseSummary, { arms: {} });
}
