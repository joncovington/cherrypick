import path from "node:path";
import { fileURLToPath } from "node:url";
import { cherrypickHome, consolePort, BIND_HOST as SHARED_BIND_HOST } from "@console/shared";

// Home and port resolution live in @console/shared because the desktop shell has to agree with this
// server about both — see that module for why they must not be duplicated.
const CHERRYPICK = cherrypickHome();

// This file lives at packages/console/server/{src,dist}/config.{ts,js} -- "src" and "dist" sit at
// the same depth under server/, so the walk up to the monorepo root is four levels either way,
// in a dev checkout or a built one. Used only to reach a sibling module's SOURCE tree (its own
// declared arms/profiles, not runtime data) for the one thing this package has no other way to
// know: whether a calibration tag is currently active or retired. Everything else this package
// reads stays under ~/.cherrypick; this is the one exception, and it stays read-only.
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

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
    gexDir: string;
    scoutDir: string;
    reviewDir: string;
    /** `data/overview/` — the morning fact packs (`morning-<session>.json`) and their narratives. */
    overviewDir: string;
    advisorDir: string;
    /** `state/advice/` — the artifacts the advisor issues and every module's loop reads. */
    adviceDir: string;
    /** packages/meic/config.risk.json (source tree) -- profiles.<tag>.enabled is the literal
        switch paper.py's all_profile_names() reads each tick. */
    meicRiskConfig: string;
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
      gexDir: path.join(data, "gex"),
      scoutDir: path.join(data, "scout"),
      reviewDir: path.join(data, "review"),
      overviewDir: path.join(data, "overview"),
      advisorDir: path.join(data, "advisor"),
      adviceDir: path.join(CHERRYPICK, "state", "advice"),
      meicRiskConfig: path.join(REPO_ROOT, "packages", "meic", "config.risk.json"),
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
    },
  };
}

/** Loopback only — never configurable. Matches the suite-wide guardrail. */
export const BIND_HOST = SHARED_BIND_HOST;
