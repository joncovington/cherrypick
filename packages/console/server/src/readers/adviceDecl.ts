import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { readJson } from "./db.js";
import { ADVISED_PREFIX, resolveAdvisedTag, type ExperimentRef } from "./experimentIndex.js";

/**
 * A module's advisor declaration: whether the advice layer is on, and which base arm/profile its
 * advised books shadow. The advised books are the one class of ledger tag that exists in NO
 * arm/profile registry — the paper loop conjures them at session start from the module config's
 * `advice` block and the session's admitted experiments — so any surface classifying tags by "is
 * it in the registry" misreads them as removed or retired while they are actively trading. This
 * is the source those surfaces consult instead, together with the advisor's own experiment rows
 * (`experimentIndex.ts`), which since 2026-09-17 are what say whether a given advised book is
 * still being written.
 */
export interface AdviceDecl {
  enabled: boolean;
  base: string | null;
}

const BASE_KEYS = ["base_profile", "base_arm", "base_book", "base_prefix"] as const;
export type AdviceBaseKey = (typeof BASE_KEYS)[number];

/** The declaration from an already-parsed module config, or null when it carries none. */
export function adviceDeclOf(doc: Record<string, unknown> | null, baseKey: AdviceBaseKey): AdviceDecl | null {
  const advice = doc?.["advice"];
  if (typeof advice !== "object" || advice === null) return null;
  const a = advice as Record<string, unknown>;
  return {
    enabled: a["enabled"] === true,
    base: typeof a[baseKey] === "string" ? (a[baseKey] as string) : null,
  };
}

function readDecl(configPath: string, baseKey: AdviceBaseKey): AdviceDecl | null {
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
 * The base any module's advice block declares, under whichever of the four key spellings the
 * modules use (`cherrypick.core.advice._legacy_base` reads the same four). Null when the config
 * is unreadable or declares none. Only a fallback: the base an advised book shadows is on the
 * advisor's experiment row first.
 */
export function declaredAdviceBase(config: ConsoleConfig, module: string): string | null {
  const file =
    module === "flies" ? config.paths.fliesConfig : path.join(config.paths.cherrypick, "config", `${module}.json`);
  const doc = readJson(file);
  const advice = doc?.["advice"];
  if (typeof advice !== "object" || advice === null) return null;
  const a = advice as Record<string, unknown>;
  for (const key of BASE_KEYS) if (typeof a[key] === "string" && a[key] !== "") return a[key] as string;
  return null;
}

/**
 * Status of one `advised:*` tag: is this book still being written?
 *
 * Since 2026-09-17 a module can run several experiments at once, each on its own book, so the
 * answer comes from the advisor's experiment rows rather than from comparing the tag to the one
 * base the config names. A tag that resolves to an experiment is active exactly while that
 * experiment is `active` in advisor.db and the module's advice layer is on. A tag no experiment
 * claims is a legacy `advised:<base>` book: history, unless there is no store to ask at all, in
 * which case the pre-change rule still applies (advice on AND this is the declared base's book —
 * `advised:control` went dormant the day MEIC's base was re-pointed at width-5, 2026-08-14).
 *
 * Null declaration AND no index is "unknown", never a guessed "retired": the badge rule everywhere
 * in this file's consumers is that a wrong "retired" on a still-trading book is worse than no badge.
 */
export function advisedTagStatus(
  tag: string,
  decl: AdviceDecl | null,
  index: ExperimentRef[] | null = null,
): "active" | "retired" | "unknown" {
  if (!tag.startsWith(ADVISED_PREFIX)) return "unknown";
  const off = decl !== null && !decl.enabled;
  const resolved = resolveAdvisedTag(tag, index);
  if (resolved.experiment !== null) {
    return resolved.experiment.status === "active" && !off ? "active" : "retired";
  }
  if (index !== null) {
    // A store exists and no experiment claims this tag. One case keeps it alive: an active
    // experiment on this base whose row carries no name or tag to resolve by (the row predates
    // the change and its book is still the legacy `advised:<base>`).
    const nameless = index.find((e) => e.status === "active" && e.tag === null && e.baseProfile === resolved.base);
    return nameless !== undefined && !off ? "active" : "retired";
  }
  if (decl === null) return "unknown";
  return decl.enabled && decl.base !== null && tag === `${ADVISED_PREFIX}${decl.base}` ? "active" : "retired";
}
