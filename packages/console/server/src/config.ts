import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cherrypickHome, consolePort, BIND_HOST as SHARED_BIND_HOST } from "@console/shared";

// Home and port resolution live in @console/shared because the desktop shell has to agree with this
// server about both — see that module for why they must not be duplicated.
const CHERRYPICK = cherrypickHome();

// This file lives at packages/console/server/{src,dist}/config.{ts,js} -- "src" and "dist" sit at
// the same depth under server/, so the walk up to the monorepo root is four levels either way,
// in a dev checkout or a built one. Used to reach a sibling module's SOURCE tree (its own declared
// arms/profiles, not runtime data) for the one thing this package has no other way to know:
// whether a calibration tag is currently active or retired; and by the System page, which asks
// git (read-only) what code the checkout holds. Everything else this package reads stays under
// ~/.cherrypick; these are the exceptions, and they stay read-only.
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

export interface ConsoleConfig {
  port: number;
  paths: {
    cherrypick: string;
    streamCacheDb: string;
    watchdogLast: string;
    orchestratorConfig: string;
    consoleData: string;
    meicDir: string;
    fliesDir: string;
    earningsDir: string;
    calendarsDir: string;
    pmccDir: string;
    curveDir: string;
    bwbDir: string;
    gexDir: string;
    reviewDir: string;
    /** `data/overview/` — the morning fact packs (`morning-<session>.json`) and their narratives. */
    overviewDir: string;
    advisorDir: string;
    /** `data/technicals/` — the technicals report (`report-<session>.json`) the Morning tab shows. */
    technicalsDir: string;
    /** `state/advice/` — the artifacts the advisor issues and every module's loop reads. */
    adviceDir: string;
    /** MEIC's arm registry -- profiles.<tag>.enabled is the literal switch paper.py's
        all_profile_names() reads each tick. MEIC's own resolution (`meic.paths.risk_profiles_path`):
        `$MEIC_RISK_CONFIG` if set, else this machine's `~/.cherrypick/config/meic.risk.json`. It
        left the repo on 2026-10-01; read it through `meicRiskConfigPath`, which applies the
        fallback below. */
    meicRiskConfig: string;
    /** The shipped control-only `packages/meic/config.risk.example.json`, which MEIC runs from
        while the home file does not exist. Absent under `$MEIC_RISK_CONFIG`: MEIC does not fall
        back from an override, so neither does this. */
    meicRiskConfigFallback?: string;
    /** ~/.cherrypick/config/flies.json (the deployed config the module actually runs off) --
        arms.<tag>.enabled is what cli.py's enabled_arms() reads. */
    fliesConfig: string;
    /** pmcc's config, in the module's OWN resolution order (deployed, then the repo's config.json,
        then the shipped example). First readable wins -- pmcc/cli.py's load_config resolves the same
        way, and a page showing a threshold the module isn't running would be worse than none. */
    pmccConfigCandidates: string[];
    /** calendars' config, in the module's OWN resolution order (`cli.load_config`): the managed
        home, then the repo's config.json, then the shipped example. Same reason as pmcc's -- a page
        showing an entry window or a dividend table the module isn't running would be worse than
        none. */
    calendarsConfigCandidates: string[];
    /** curve's config, in the module's OWN resolution order (`cli.load_config`): the managed home,
        then the repo's config.json, then the shipped example. Same reason as pmcc's/calendars' --
        a page showing a contango_max or hook_threshold the module isn't running would be worse
        than none. */
    curveConfigCandidates: string[];
  };
}

export function loadConfig(): ConsoleConfig {
  const data = path.join(CHERRYPICK, "data");
  return {
    port: consolePort(CHERRYPICK),
    paths: {
      cherrypick: CHERRYPICK,
      streamCacheDb: path.join(data, "marketdata", "stream_cache.db"),
      watchdogLast: path.join(CHERRYPICK, "state", "watchdog.last.json"),
      orchestratorConfig: path.join(CHERRYPICK, "config.json"),
      consoleData: path.join(data, "console"),
      meicDir: path.join(data, "meic"),
      fliesDir: path.join(data, "flies"),
      earningsDir: path.join(data, "earnings"),
      calendarsDir: path.join(data, "calendars"),
      pmccDir: path.join(data, "pmcc"),
      curveDir: path.join(data, "curve"),
      bwbDir: path.join(data, "bwb"),
      gexDir: path.join(data, "gex"),
      reviewDir: path.join(data, "review"),
      overviewDir: path.join(data, "overview"),
      advisorDir: path.join(data, "advisor"),
      technicalsDir: path.join(data, "technicals"),
      adviceDir: path.join(CHERRYPICK, "state", "advice"),
      ...meicRiskPaths(),
      fliesConfig: path.join(CHERRYPICK, "config", "flies.json"),
      pmccConfigCandidates: [
        path.join(CHERRYPICK, "config", "pmcc.json"),
        path.join(REPO_ROOT, "packages", "pmcc", "config.json"),
        path.join(REPO_ROOT, "packages", "pmcc", "config.example.json"),
      ],
      calendarsConfigCandidates: [
        path.join(CHERRYPICK, "config", "calendars.json"),
        path.join(REPO_ROOT, "packages", "calendars", "config.json"),
        path.join(REPO_ROOT, "packages", "calendars", "config.example.json"),
      ],
      curveConfigCandidates: [
        path.join(CHERRYPICK, "config", "curve.json"),
        path.join(REPO_ROOT, "packages", "curve", "config.json"),
        path.join(REPO_ROOT, "packages", "curve", "config.example.json"),
      ],
    },
  };
}

function meicRiskPaths(): { meicRiskConfig: string; meicRiskConfigFallback?: string } {
  const override = process.env["MEIC_RISK_CONFIG"];
  if (override !== undefined && override !== "") return { meicRiskConfig: override };
  return {
    meicRiskConfig: path.join(CHERRYPICK, "config", "meic.risk.json"),
    meicRiskConfigFallback: path.join(REPO_ROOT, "packages", "meic", "config.risk.example.json"),
  };
}

/**
 * The MEIC registry file MEIC itself is running from: the home file when it exists, else the
 * shipped example. Existence, not readability, decides, as in `risk_profiles_path` -- a malformed
 * home file is what MEIC reads (and fails on), so the console must not quietly show the example.
 */
export function meicRiskConfigPath(config: ConsoleConfig): string {
  const own = config.paths.meicRiskConfig;
  const fallback = config.paths.meicRiskConfigFallback;
  if (fallback === undefined || fs.existsSync(own)) return own;
  return fallback;
}

/** Loopback only — never configurable. Matches the suite-wide guardrail. */
export const BIND_HOST = SHARED_BIND_HOST;
