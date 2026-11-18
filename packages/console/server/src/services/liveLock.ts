import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { readJson } from "../readers/db.js";

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

export interface ModuleGate {
  id: string;
  /**
   * null = a module with a live path whose switch cannot be read. Unknown is not the same as off,
   * and must never render as one. A module with no live path is false: it cannot trade live.
   */
  liveEnabled: boolean | null;
  /** Whether the module has a live loop at all (`LIVE_PATH_MODULES`). False = paper by construction. */
  livePath: boolean;
  /** The module's switch in the orchestrator config (`modules.<id>.enabled`); null when not listed. */
  moduleEnabled: boolean | null;
  /** flies only: the human attestation string's presence (never its text). */
  gate0Confirmed?: boolean;
}

/**
 * The modules with a live loop. A MIRROR of the orchestrator's `config.LIVE_PATH_MODULES`, which a
 * test there keeps equal to the packages that ship a `live_loop.py`; `test/live-path-mirror.test.ts`
 * pins this copy to that one. Kept here rather than bridged because the lock is polled.
 */
export const LIVE_PATH_MODULES: readonly string[] = ["bwb", "flies", "meic"];

/** Read from the module's own file and ignored there: earnings went paper-only in v0.12.0. */
const PAPER_ONLY_MODULES: readonly string[] = ["earnings"];

function readSwitch(doc: Record<string, unknown> | null): { live: boolean | null; gate0Confirmed?: boolean } {
  if (doc === null) return { live: null };
  const top = doc["enable_live_trading"];
  if (typeof top === "boolean") return { live: top };
  const live = doc["live"];
  if (typeof live === "object" && live !== null) {
    const nested = live as Record<string, unknown>;
    const gate0 = nested["gate0_confirmed"];
    return {
      live: typeof nested["enabled"] === "boolean" ? (nested["enabled"] as boolean) : null,
      gate0Confirmed: typeof gate0 === "string" && gate0.trim() !== "",
    };
  }
  return { live: null };
}

function moduleSwitch(config: ConsoleConfig, id: string): boolean | null {
  const modules = (readJson(config.paths.orchestratorConfig) ?? {})["modules"];
  if (typeof modules !== "object" || modules === null || Array.isArray(modules)) return null;
  const entry = (modules as Record<string, unknown>)[id];
  if (typeof entry !== "object" || entry === null) return null;
  return (entry as Record<string, unknown>)["enabled"] === true;
}

export interface LockStatus {
  halted: boolean;
  haltFlagPath: string;
  modules: ModuleGate[];
  /** flies' per-day arm record — the third gate, and the only one that expires by itself. */
  fliesArm: { armed: boolean; date: string | null; at: string | null; stale: boolean };
  sessionDate: string;
}

/**
 * A module's live gate, covering BOTH conventions the suite uses: a top-level
 * `enable_live_trading` (meic) and the nested `live.enabled` (flies, bwb). Reading only the
 * top-level form reports a nested-gate module as paper-only even while armed — the same trap
 * `liveops._live_enabled` documents on the Python side.
 *
 * Whether the module has a live path decides what a missing switch means. With one, a switch that
 * cannot be read is unknown; without one, the module is paper by construction and reads false.
 * The switch is still read for every module except earnings, so one found ON outside the live-path
 * set is reported as on, never hidden behind "no live path".
 */
export function readModuleGate(config: ConsoleConfig, id: string): ModuleGate {
  const livePath = LIVE_PATH_MODULES.includes(id);
  const moduleEnabled = moduleSwitch(config, id);
  if (PAPER_ONLY_MODULES.includes(id)) return { id, liveEnabled: false, livePath, moduleEnabled };
  const { live, gate0Confirmed } = readSwitch(readJson(path.join(config.paths.cherrypick, "config", `${id}.json`)));
  const liveEnabled = live === true ? true : livePath ? live : false;
  return { id, liveEnabled, livePath, moduleEnabled, ...(gate0Confirmed === undefined ? {} : { gate0Confirmed }) };
}

/** Today in ET — the session date every per-day gate is measured against. */
export function sessionDateEt(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

// There was a git check here ("uncommitted changes in packages/meic/config.risk.json"). It went
// when MEIC's arm registry left the repo for `~/.cherrypick/config/meic.risk.json` (2026-10-01):
// a file outside the checkout has no working tree to dirty.

/**
 * A path as the page shows it: under the home folder it starts `~/`, so the user name never appears
 * on a page (or in a screenshot of it). Forward slashes on every OS. A path elsewhere is shown whole.
 */
export function displayPath(p: string, home = os.homedir()): string {
  const rel = path.relative(home, p);
  const inside = rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
  return (inside ? `~/${rel}` : p).split(path.sep).join("/");
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
    haltFlagPath: displayPath(haltFlagPath),
    modules: ids.map((id) => readModuleGate(config, id)),
    fliesArm: {
      // Armed means armed FOR TODAY. A record from a previous day is a record the live loop
      // self-disarms on, so showing it as armed would be showing a gate that is already shut.
      armed: armDate === today,
      date: armDate,
      at: typeof arm?.["at"] === "string" ? (arm["at"] as string) : null,
      stale: armDate !== null && armDate !== today,
    },
    sessionDate: today,
  };
}
