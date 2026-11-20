import type { LadderLiveChecks, LadderLiveGate, LadderLiveIntent, LadderLiveStatus } from "@console/shared";
import { maskAccount } from "../readers/ladderLive.js";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The ladder's live readiness, read from the module that owns it (`python -m
 * cherrypick.ladder.live_loop --status`): the nine gates each with its reason, dry-run or live, armed
 * today, the halt flag, anything an uncertain submit still holds, and the last tick's checks (the
 * positions cross-check, the declared splits, the standing dry-run intents). Whether the loop may act
 * is a derivation over the config, the suite switch, the arm record, the designated account and the
 * halt flag, so the console asks the module's `live_gates` rather than keeping a second copy that could
 * disagree with `/live-ladder-start`. `--status` reads files and the keyring and never calls a broker.
 *
 * A failed or malformed answer is `{ok: false, error}`, never a "not ready" verdict, and never a
 * "ready" one: a dead bridge must not read as either. This file spawns one read-only verb and holds no
 * verb that arms, disarms or halts.
 */

const UNAVAILABLE = "ladder live status unavailable — is cherrypick-ladder installed?";

type Json = Record<string, unknown>;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const obj = (v: unknown): Json => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Json) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strings = (v: unknown): string[] => list(v).filter((x): x is string => typeof x === "string");

const FAILED: Omit<LadderLiveStatus, "ok" | "error"> = {
  mode: null,
  note: null,
  today: null,
  ready: null,
  newBuysAllowed: null,
  armedToday: null,
  armedFor: null,
  account: null,
  haltFlag: null,
  unmetGates: [],
  gates: [],
  held: { unresolved: [], unrecorded: 0 },
  checks: null,
};

function gate(g: unknown): LadderLiveGate[] {
  const r = obj(g);
  const name = str(r["name"]);
  if (name === null || typeof r["ok"] !== "boolean") return [];
  return [{ name, ok: r["ok"], reason: typeof r["reason"] === "string" ? r["reason"] : "", blocks: r["blocks"] === "new_buys" ? "new_buys" : "all" }];
}

function intent(i: unknown): LadderLiveIntent {
  const r = obj(i);
  return { what: str(r["what"]) ?? "", reason: str(r["reason"]), lot: str(r["lot"]), shares: num(r["shares"]), limit: num(r["limit"]), tif: str(r["tif"]) };
}

/** `live_tick.read_checks`: `{}` before the loop has ticked, which is no checks and not an empty clean bill. */
export function normaliseChecks(raw: unknown): LadderLiveChecks | null {
  const c = obj(raw);
  if (Object.keys(c).length === 0) return null;
  const positions = obj(c["positions"]);
  const splits = obj(c["splits"]);
  return {
    at: str(c["at"]),
    date: str(c["date"]),
    positionsReadable: bool(positions["readable"]),
    funds: Object.entries(obj(positions["funds"])).map(([fund, f]) => ({
      fund,
      ledger: num(obj(f)["ledger"]),
      broker: num(obj(f)["broker"]),
      status: str(obj(f)["status"]),
      suspectedSplit: str(obj(f)["suspected_split"]),
    })),
    blocked: Object.entries(obj(positions["blocked"])).map(([fund, reason]) => ({ fund, reason: typeof reason === "string" ? reason : "" })),
    splitsDeclared: list(splits["declared"]).map((s) => {
      const r = obj(s);
      return { fund: str(r["fund"]) ?? "", exDate: str(r["ex_date"]) ?? "", ratio: `${String(num(r["to"]) ?? "?")}:${String(num(r["per"]) ?? "?")}` };
    }),
    splitProblems: strings(splits["problems"]),
    intents: list(c["intents"]).map(intent),
  };
}

/** The module's `--status` document into the console's, or a failure naming what is wrong. */
export function shapeStatus(res: SpawnedJson): LadderLiveStatus {
  if (!res.ok || res.json === null) return { ok: false, error: res.error ?? "no answer", ...FAILED };
  const j = res.json;
  if (j["ok"] === false) return { ok: false, error: typeof j["error"] === "string" ? j["error"] : "status refused", ...FAILED };
  const gates = list(j["gates"]).flatMap(gate);
  if (gates.length === 0 || typeof j["ready"] !== "boolean") {
    return { ok: false, error: `${UNAVAILABLE} — the status has no gates (the ladder package may be out of date)`, ...FAILED };
  }
  const held = obj(j["held"]);
  const dry = j["dry_run"];
  // A verdict that says ready while a gate fails contradicts itself: take the stricter reading.
  const ready = j["ready"] && gates.filter((g) => g.blocks === "all").every((g) => g.ok);
  return {
    ok: true,
    error: null,
    // Only an explicit `false` is live, as the module reads `live.dry_run`: anything else stays dry-run.
    mode: dry === false ? "live" : "dry-run",
    note: str(j["note"]),
    today: str(j["today"]),
    ready,
    newBuysAllowed: ready && gates.every((g) => g.ok) && j["new_buys_allowed"] !== false,
    armedToday: bool(j["armed_today"]),
    armedFor: str(j["armed_for"]),
    account: maskAccount(j["account"]),
    haltFlag: bool(j["halt_flag"]),
    unmetGates: gates.filter((g) => !g.ok).map((g) => g.name),
    gates,
    held: { unresolved: strings(held["unresolved"]), unrecorded: Object.keys(obj(held["unrecorded"])).length },
    checks: normaliseChecks(j["checks"]),
  };
}

async function spawnCaller(): Promise<SpawnedJson> {
  return spawnModuleCliAsync(["-m", "cherrypick.ladder.live_loop", "--status"], UNAVAILABLE);
}

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
let caller: Caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setLadderLiveCaller(fn?: Caller): void {
  caller = fn ?? spawnCaller;
}

// The page polls every 15 s; one subprocess per ~10 s is plenty for a readout a person reads.
const TTL_MS = 10_000;
const cache = new AsyncMemo<LadderLiveStatus>();

export function readLadderLiveStatus(now = Date.now()): Promise<LadderLiveStatus> {
  return cache.get("status", TTL_MS, now, async () => shapeStatus(await caller()));
}

/** Drop the memoised readout. Tests only. */
export function resetLadderLiveStatusCache(): void {
  cache.clear();
}
