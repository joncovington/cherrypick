import fs from "node:fs";
import path from "node:path";
import type { ArmModule, RetiredArmsPayload } from "@console/shared";
import { meicRiskConfigPath, type ConsoleConfig } from "../config.js";

/** Where each module keeps its arm registry. The module configs sit together in the config dir. */
function registryFile(config: ConsoleConfig, module: ArmModule): string {
  if (module === "meic") return meicRiskConfigPath(config);
  return path.join(path.dirname(config.paths.fliesConfig), `${module}.json`);
}

/** The registry under whichever spelling the file uses: the suite reads `arms`, `books` and
 *  `profiles` alike (`cherrypick.core.config`), so this does too. */
function registryOf(doc: Record<string, unknown>): Record<string, unknown> {
  for (const key of ["arms", "books", "profiles"]) {
    const v = doc[key];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
  }
  return {};
}

/** Retired = the module's own config says `enabled: false`. A missing or unreadable file lists
 *  nothing, so the filters fall back to showing every arm rather than hiding live ones. */
export function readRetiredArms(config: ConsoleConfig): RetiredArmsPayload {
  const modules: ArmModule[] = ["flies", "meic", "bwb", "curve", "pmcc", "contango"];
  const retired = {} as Record<ArmModule, string[]>;
  for (const module of modules) {
    let names: string[] = [];
    try {
      const doc = JSON.parse(fs.readFileSync(registryFile(config, module), "utf8")) as Record<string, unknown>;
      names = Object.entries(registryOf(doc))
        .filter(([, v]) => v !== null && typeof v === "object" && (v as Record<string, unknown>)["enabled"] === false)
        .map(([name]) => name)
        .sort();
    } catch {
      names = [];
    }
    retired[module] = names;
  }
  return { retired };
}
