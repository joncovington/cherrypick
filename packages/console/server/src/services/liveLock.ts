import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import type { ConsoleConfig } from "../config.js";

/**
 * The live-lock read: the suite halt flag, each module's live gate, and flies' per-day arm record.
 *
 * Three separate things, deliberately not merged into one boolean. The halt flag
 * (`state/halt-live.flag`) is the suite-wide stop every live loop polls; a module's
 * `enable_live_trading` is its own plan-gated switch; flies additionally needs a per-day arm record
 * written only by its human-confirmed arm command. Clearing the halt flag therefore arms nothing by
 * itself, and the UI has to be able to say so — which it can only do if it reads all three.
 *
 * File-only and read-only, cheap enough for the page to poll. The one thing here that WRITES is not
 * here at all: the halt toggle goes through the orchestrator's `liveops.set_halt` via the bridge.
 */

function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export interface ModuleGate {
  id: string;
  /** null = no readable config. Unknown is not the same as off, and must never render as one. */
  liveEnabled: boolean | null;
  /** flies only: the human attestation string's presence (never its text). */
  gate0Confirmed?: boolean;
}

export interface LockStatus {
  halted: boolean;
  haltFlagPath: string;
  modules: ModuleGate[];
  /** flies' per-day arm record — the third gate, and the only one that expires by itself. */
  fliesArm: { armed: boolean; date: string | null; at: string | null; stale: boolean };
  /** meic-risk lives in the source tree, so editing it dirties the working tree. */
  meicRiskDirty: boolean | null;
  sessionDate: string;
}

/**
 * A module's live gate, covering BOTH conventions the suite uses: a top-level
 * `enable_live_trading` (meic, earnings) and the nested `live.enabled` (flies, and now calendars/
 * pmcc as inert placeholders — see their config `_live_note`: no loop reads the field yet, it exists
 * only so this surface can report "paper only" instead of blurring "no live path built" with "gate
 * file missing"). Reading only the top-level form reports a nested-gate module as paper-only even
 * while armed — the same trap `liveops._live_enabled` documents on the Python side.
 */
export function readModuleGate(config: ConsoleConfig, id: string): ModuleGate {
  const doc = readJson(path.join(config.paths.cherrypick, "config", `${id}.json`));
  if (doc === null) return { id, liveEnabled: null };
  const top = doc["enable_live_trading"];
  if (typeof top === "boolean") return { id, liveEnabled: top };
  const live = doc["live"];
  if (typeof live === "object" && live !== null) {
    const nested = live as Record<string, unknown>;
    const gate0 = nested["gate0_confirmed"];
    return {
      id,
      liveEnabled: typeof nested["enabled"] === "boolean" ? (nested["enabled"] as boolean) : null,
      gate0Confirmed: typeof gate0 === "string" && gate0.trim() !== "",
    };
  }
  return { id, liveEnabled: null };
}

/** Today in ET — the session date every per-day gate is measured against. */
export function sessionDateEt(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

let riskDirtyCache: { at: number; value: boolean | null } | null = null;

/**
 * Whether packages/meic/config.risk.json has uncommitted changes. Best-effort and cached: it is a
 * courtesy note on a section, never a gate, so a missing git or a slow call degrades to "unknown"
 * rather than holding up the page.
 */
export function meicRiskDirty(config: ConsoleConfig, now = Date.now()): boolean | null {
  if (riskDirtyCache !== null && now - riskDirtyCache.at < 30_000) return riskDirtyCache.value;
  let value: boolean | null = null;
  try {
    const file = config.paths.meicRiskConfig;
    const out = spawnSync("git", ["-C", path.dirname(file), "status", "--porcelain", "--", path.basename(file)], {
      encoding: "utf-8",
      timeout: 5_000,
      windowsHide: true,
    });
    if (out.error === undefined && out.status === 0) value = out.stdout.trim() !== "";
  } catch {
    value = null;
  }
  riskDirtyCache = { at: now, value };
  return value;
}

/** Reset the git-dirty cache. Tests only. */
export function resetLockCaches(): void {
  riskDirtyCache = null;
}

export function readLockStatus(config: ConsoleConfig): LockStatus {
  const haltFlagPath = path.join(config.paths.cherrypick, "state", "halt-live.flag");
  const orchestrator = readJson(config.paths.orchestratorConfig) ?? {};
  const modulesRaw = orchestrator["modules"];
  const ids =
    typeof modulesRaw === "object" && modulesRaw !== null && !Array.isArray(modulesRaw)
      ? Object.keys(modulesRaw as Record<string, unknown>)
      : ["meic", "flies", "earnings", "calendars", "pmcc"];

  const arm = readJson(path.join(config.paths.cherrypick, "state", "flies-live-arm.json"));
  const armDate = typeof arm?.["date"] === "string" ? (arm["date"] as string) : null;
  const today = sessionDateEt();

  return {
    halted: fs.existsSync(haltFlagPath),
    haltFlagPath,
    modules: ids.map((id) => readModuleGate(config, id)),
    fliesArm: {
      // Armed means armed FOR TODAY. A record from a previous day is a record the live loop
      // self-disarms on, so showing it as armed would be showing a gate that is already shut.
      armed: armDate === today,
      date: armDate,
      at: typeof arm?.["at"] === "string" ? (arm["at"] as string) : null,
      stale: armDate !== null && armDate !== today,
    },
    meicRiskDirty: meicRiskDirty(config),
    sessionDate: today,
  };
}
