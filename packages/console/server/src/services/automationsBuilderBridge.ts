import type { BuilderAutomation, BuilderBlockState, BuilderResult } from "@console/shared";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Builder page's read (#1180): every automation block by block from the automations package itself
 * (`python -m cherrypick.automations.cli builder`), each block's backtest label, its start, its forward
 * rule and whether our own data covers a backtest of it. Read-only; the package owns every label, so
 * nothing here is derived a second time. Memoised briefly: it changes only when the config does.
 */

const UNAVAILABLE =
  "the automation builder is unavailable — the automations package must be installed (pip install -e packages/automations)";
const TTL_MS = 60_000;
const STATES = new Set<BuilderBlockState>(["backtested", "changed", "not_backtested"]);

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = () => spawnModuleCliAsync(["-m", "cherrypick.automations.cli", "builder"], UNAVAILABLE);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAutomationsBuilderCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

function obj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/** The CLI's rows, each field checked; a block with an unknown label reads as not backtested. */
export function shapeBuilder(json: Record<string, unknown> | null): BuilderAutomation[] {
  const rows = Array.isArray(json?.["automations"]) ? (json!["automations"] as unknown[]) : [];
  return rows.map((raw) => {
    const r = obj(raw);
    const reg = obj(r["register"]);
    const blocks = Array.isArray(r["blocks"]) ? (r["blocks"] as unknown[]) : [];
    const sug = r["suggestion"] === null || r["suggestion"] === undefined ? null : obj(r["suggestion"]);
    return {
      name: String(r["name"] ?? ""),
      group: str(r["group"]),
      symbol: String(r["symbol"] ?? ""),
      enabled: r["enabled"] !== false,
      note: str(r["note"]),
      start: str(r["start"]),
      register: typeof reg["item"] === "number" ? { item: reg["item"], verdict: String(reg["verdict"] ?? "") } : null,
      summary: String(r["summary"] ?? ""),
      blocks: blocks.map((b) => {
        const x = obj(b);
        const state = STATES.has(x["state"] as BuilderBlockState) ? (x["state"] as BuilderBlockState) : "not_backtested";
        return { block: String(x["block"] ?? ""), state, keys: obj(x["keys"]) };
      }),
      forward: r["forward"] ? obj(r["forward"]) : null,
      suggestion: sug
        ? {
            blocks: Array.isArray(sug["blocks"]) ? (sug["blocks"] as unknown[]).map(String) : [],
            window: Array.isArray(sug["window"]) ? [String(sug["window"][0] ?? ""), String(sug["window"][1] ?? "")] : ["", ""],
            needs: (Array.isArray(sug["needs"]) ? (sug["needs"] as unknown[]) : []).map((n) => {
              const y = obj(n);
              const near = y["nearest"] ? obj(y["nearest"]) : null;
              return {
                field: String(y["field"] ?? ""),
                ...(str(y["expiry"]) ? { expiry: String(y["expiry"]) } : {}),
                cadence: String(y["cadence"] ?? ""),
                covered: y["covered"] === true,
                source: str(y["source"]),
                ...(near
                  ? { nearest: { source: String(near["source"] ?? ""), first: str(near["first"]), last: str(near["last"]), why: str(near["why"]) } }
                  : {}),
              };
            }),
            possible: sug["possible"] === true,
          }
        : null,
    };
  });
}

/** Forget the memoised read, so the page shows a save at once. */
export function invalidateAutomationsBuilder(): void {
  memo.clear();
}

export async function readAutomationsBuilder(now: number = Date.now()): Promise<BuilderResult> {
  const res = await memo.get("builder", TTL_MS, now, () => caller());
  if (!res.ok) return { date: null, automations: [], error: res.error };
  return { date: str(res.json?.["date"]), automations: shapeBuilder(res.json), error: null };
}
