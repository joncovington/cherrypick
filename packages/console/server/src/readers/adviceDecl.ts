import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";

/**
 * A module's advisor declaration: whether the advice layer is on, and which base arm/profile its
 * synthetic `advised:<base>` book shadows. The advised books are the one class of ledger tag that
 * exists in NO arm/profile registry — the paper loop conjures them at session start from the
 * module config's `advice` block — so any surface classifying tags by "is it in the registry"
 * misreads them as removed or retired while they are actively trading. This is the source those
 * surfaces consult instead.
 */
export interface AdviceDecl {
  enabled: boolean;
  base: string | null;
}

/** The declaration from an already-parsed module config, or null when it carries none. */
export function adviceDeclOf(doc: Record<string, unknown> | null, baseKey: "base_profile" | "base_arm"): AdviceDecl | null {
  const advice = doc?.["advice"];
  if (typeof advice !== "object" || advice === null) return null;
  const a = advice as Record<string, unknown>;
  return {
    enabled: a["enabled"] === true,
    base: typeof a[baseKey] === "string" ? (a[baseKey] as string) : null,
  };
}

function readDecl(configPath: string, baseKey: "base_profile" | "base_arm"): AdviceDecl | null {
  try {
    return adviceDeclOf(JSON.parse(fs.readFileSync(configPath, "utf-8")) as Record<string, unknown>, baseKey);
  } catch {
    return null;
  }
}

/** MEIC declares advice in the deployed ~/.cherrypick/config/meic.json, keyed `base_profile`. */
export function meicAdviceDecl(config: ConsoleConfig): AdviceDecl | null {
  return readDecl(path.join(config.paths.cherrypick, "config", "meic.json"), "base_profile");
}

/** Flies declares advice in the same deployed config its arms live in, keyed `base_arm`. */
export function fliesAdviceDecl(config: ConsoleConfig): AdviceDecl | null {
  return readDecl(config.paths.fliesConfig, "base_arm");
}

/**
 * Status of one `advised:<base>` tag against the declaration. Active only while the advice layer
 * is on AND this tag is the book it currently produces — `advised:control` went dormant the day
 * MEIC's base was re-pointed at width-5 (2026-08-14), even though advice stayed enabled
 * throughout. Null declaration (unreadable config, no advice block) is "unknown", never a guessed
 * "retired": the badge rule everywhere else in this file's consumers is that a wrong "retired" on
 * a still-trading book is worse than no badge.
 */
export function advisedTagStatus(tag: string, decl: AdviceDecl | null): "active" | "retired" | "unknown" {
  if (decl === null) return "unknown";
  return decl.enabled && decl.base !== null && tag === `advised:${decl.base}` ? "active" : "retired";
}
