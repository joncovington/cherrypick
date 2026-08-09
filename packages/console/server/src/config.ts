import os from "node:os";
import path from "node:path";
import fs from "node:fs";

const HOME = os.homedir();
const CHERRYPICK = path.join(HOME, ".cherrypick");

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

function loadUserConfig(): Record<string, unknown> {
  const p = path.join(CHERRYPICK, "config", "console.json");
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export function loadConfig(): ConsoleConfig {
  const user = loadUserConfig();
  const serve = (user["serve"] ?? {}) as Record<string, unknown>;
  // 5060/5061 are on Chrome's unsafe-port list (SIP) — the default deliberately avoids them.
  const port = typeof serve["port"] === "number" ? serve["port"] : 5070;
  const data = path.join(CHERRYPICK, "data");
  return {
    port,
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
export const BIND_HOST = "127.0.0.1";
