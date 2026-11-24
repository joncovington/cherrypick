/**
 * The Config page's live tab: which arm each live loop runs, and what may change it.
 *
 * The console decides nothing here. MEIC's own answer comes from the orchestrator's `live_arm_options`
 * (the arm live runs now, the arms that have a paper twin, and why a change is refused right now: not
 * flat, not settled, ...), reached through the config bridge like every other Config read. What the
 * bridge does not cover is read off files and marked not editable, with the reason: another login's arm
 * sits under the guarded `/live_accounts`, and the ladder's status does not say whether it holds lots.
 *
 * Each arm's own plain settings ride along (from the machine's arm registry) so the page can say what a
 * change would change. Nothing is written here; the one write is `set_live_arm`, in `routes/configOps.ts`.
 */

import path from "node:path";
import type { LiveArmChoice, LiveArmLoop, LiveArmPayload, LiveArmSetting } from "@console/shared";
import { meicRiskConfigPath, type ConsoleConfig } from "../config.js";
import { callConfigCli } from "../services/configBridge.js";
import { readModuleGate } from "../services/liveLock.js";
import { readJson } from "./db.js";
import { meicLiveAccounts } from "./meicLive.js";

const MARKERS = new Set(["enabled", "follows_live", "shadow_of"]);

function record(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

/** An arm's plain settings: a string, number, boolean or null per key; notes, markers and nested values left out. */
export function plainSettings(profile: unknown): Record<string, LiveArmSetting> {
  const out: Record<string, LiveArmSetting> = {};
  for (const [k, v] of Object.entries(record(profile))) {
    if (k.startsWith("_") || MARKERS.has(k)) continue;
    if (v === null || ["string", "number", "boolean"].includes(typeof v)) out[k] = v as LiveArmSetting;
  }
  return out;
}

function registryProfiles(config: ConsoleConfig): Record<string, unknown> {
  if (!config.paths.meicRiskConfig) return {};
  return record(readJson(meicRiskConfigPath(config))?.["profiles"]);
}

const BY_HAND_LOGIN = "Another login's arm is edited by hand: its live block is guarded as a whole.";
const BY_HAND_LADDER =
  "The ladder's arm is edited by hand: its status does not say whether it holds lots, so a change cannot be judged safe here.";

export async function readLiveArm(config: ConsoleConfig): Promise<LiveArmPayload> {
  const meicCfg = readJson(path.join(config.paths.cherrypick, "config", "meic.json")) ?? {};
  const meicLive = record(meicCfg["live"]);
  const loops: LiveArmLoop[] = [];
  let bridge: LiveArmPayload["bridge"] = "ok";
  let error: string | null = null;

  if (Object.keys(meicLive).length > 0) {
    const symbol = str(meicLive["symbol"]);
    const options = await callConfigCli({ op: "live_arm_options" });
    if (options.ok) {
      const profiles = registryProfiles(config);
      const offered = Array.isArray(options["arms"]) ? (options["arms"] as unknown[]) : [];
      const arms: LiveArmChoice[] = offered
        .map((a) => record(a))
        .filter((a) => str(a["arm"]) !== null)
        .map((a) => ({ arm: str(a["arm"])!, twin: str(a["twin"]) ?? "", settings: plainSettings(profiles[str(a["arm"])!]) }));
      const blockers = Array.isArray(options["blockers"]) ? (options["blockers"] as unknown[]).filter((b): b is string => typeof b === "string") : [];
      loops.push({
        loop: "meic",
        label: symbol === null ? "meic" : `meic · ${symbol}`,
        symbol,
        current: str(options["current"]),
        editable: true,
        note: arms.length === 0 ? "No arm has a paper twin yet, so there is nothing to choose between." : null,
        arms,
        blockers,
        canChange: options["can_change"] === true && arms.length > 0,
      });
    } else {
      bridge = "unavailable";
      error = options.error;
      loops.push({
        loop: "meic",
        label: symbol === null ? "meic" : `meic · ${symbol}`,
        symbol,
        current: str(meicLive["arm"]),
        editable: false,
        note: "The orchestrator could not be reached, so the arm cannot be changed from here.",
        arms: [],
        blockers: [],
        canChange: false,
      });
    }
    const accounts = record(meicCfg["live_accounts"]);
    for (const login of meicLiveAccounts(config)) {
      const entry = record(accounts[login]);
      const sym = str(entry["symbol"]);
      loops.push({
        loop: `meic.${login}`,
        label: sym === null ? `meic · ${login}` : `meic · ${sym} · ${login}`,
        symbol: sym,
        current: str(entry["arm"]),
        editable: false,
        note: BY_HAND_LOGIN,
        arms: [],
        blockers: [],
        canChange: false,
      });
    }
  }

  const ladderLive = record(readJson(path.join(config.paths.cherrypick, "config", "ladder.json"))?.["live"]);
  if (readModuleGate(config, "ladder").moduleEnabled === true && Object.keys(ladderLive).length > 0) {
    const symbols = Array.isArray(ladderLive["symbols"]) ? (ladderLive["symbols"] as unknown[]).filter((s): s is string => typeof s === "string") : [];
    loops.push({
      loop: "ladder",
      label: "ladder",
      symbol: symbols.length === 0 ? null : symbols.join("/"),
      current: str(ladderLive["arm"]),
      editable: false,
      note: BY_HAND_LADDER,
      arms: [],
      blockers: [],
      canChange: false,
    });
  }
  return { loops, bridge, error };
}
