import type { AutomationsGroupsRead, AutomationsGroupUnit, AutomationsLine } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";

/**
 * Every automation group read as one unit (`python -m cherrypick.automations.cli groups-read`): its
 * members' line stacked, net and drawdown against its allocation, the most risk held at once against
 * its cap, the entries the cap or a share refused, and how its members move together. The package
 * computes all of it; here its snake-case line is put in the performance page's own line shape, and
 * nothing else.
 */

const UNAVAILABLE =
  "the group read is unavailable — the automations package must be installed (pip install -e packages/automations)";
const TTL_MS = 60_000;

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = () => spawnModuleCliAsync(["-m", "cherrypick.automations.cli", "groups-read"], UNAVAILABLE);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setGroupsReadCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** The package's line (`groupread.line`) in the performance page's shape. */
export function lineFrom(raw: unknown): AutomationsLine {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const curve = Array.isArray(r["curve"]) ? (r["curve"] as Array<Record<string, unknown>>) : [];
  return {
    trades: num(r["trades"]) ?? 0,
    net: num(r["net"]) ?? 0,
    gross: num(r["gross"]) ?? 0,
    fees: num(r["fees"]) ?? 0,
    settlement: num(r["settlement"]) ?? 0,
    slippage: num(r["slippage"]) ?? 0,
    netStress: num(r["net_stress"]) ?? 0,
    netMid: num(r["net_mid"]) ?? 0,
    wins: num(r["wins"]) ?? 0,
    mean: num(r["mean"]),
    worst: num(r["worst"]),
    drawdown: num(r["drawdown"]) ?? 0,
    curve: curve.map((p) => ({ x: String(p["x"] ?? ""), y: num(p["y"]) ?? 0 })),
  };
}

export async function readGroupUnits(now: number = Date.now()): Promise<AutomationsGroupsRead> {
  const res = await memo.get("read", TTL_MS, now, () => caller());
  const empty: AutomationsGroupsRead = { groups: [], ungrouped: { members: [], line: lineFrom({}) }, error: null };
  if (!res.ok || res.json === null) return { ...empty, error: res.ok ? "the group read gave nothing" : res.error };
  if (res.json["ok"] !== true) return { ...empty, error: String(res.json["error"] ?? "the group read failed") };
  const groups = Array.isArray(res.json["groups"]) ? (res.json["groups"] as Array<Record<string, unknown>>) : [];
  const loose = (res.json["ungrouped"] ?? {}) as Record<string, unknown>;
  return {
    groups: groups.map((g) => ({ ...(g as unknown as AutomationsGroupUnit), line: lineFrom(g["line"]) })),
    ungrouped: { members: Array.isArray(loose["members"]) ? (loose["members"] as string[]) : [], line: lineFrom(loose["line"]) },
    error: null,
  };
}
