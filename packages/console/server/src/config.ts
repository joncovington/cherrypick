import path from "node:path";
import { cherrypickHome, consolePort, BIND_HOST as SHARED_BIND_HOST } from "@console/shared";

// Home and port resolution live in @console/shared because the desktop shell has to agree with this
// server about both — see that module for why they must not be duplicated.
const CHERRYPICK = cherrypickHome();

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
    gexDir: string;
    scoutDir: string;
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
      gexDir: path.join(data, "gex"),
      scoutDir: path.join(data, "scout"),
    },
  };
}

/** Loopback only — never configurable. Matches the suite-wide guardrail. */
export const BIND_HOST = SHARED_BIND_HOST;
