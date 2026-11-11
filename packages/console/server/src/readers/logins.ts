import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { readJson } from "./db.js";

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** The further broker logins the suite reads (`broker.logins` in the orchestrator config), main
 *  never listed: the names only, never a secret. */
export function brokerLogins(config: ConsoleConfig): string[] {
  const suite = readJson(path.join(config.paths.cherrypick, "config.json")) ?? {};
  const logins = rec(suite["broker"])["logins"];
  return (Array.isArray(logins) ? logins : [])
    .map((e) => rec(e)["name"])
    .filter((n): n is string => typeof n === "string" && n !== "main");
}

/** The orchestrator's `streamer.instances`: each a broker login in `broker.logins` (its
 *  `config.streamer_instances`); an unlisted name has no secrets to stream with and is left out. */
export function streamerInstances(config: ConsoleConfig): string[] {
  const suite = readJson(path.join(config.paths.cherrypick, "config.json")) ?? {};
  const streamer = rec(suite["streamer"]);
  if (streamer["enabled"] !== true) return [];
  const known = new Set(brokerLogins(config));
  const raw = Array.isArray(streamer["instances"]) ? streamer["instances"] : [];
  return [...new Set(raw.filter((n): n is string => typeof n === "string" && known.has(n)))];
}
