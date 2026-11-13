import type { ConfigModelPayload } from "@console/shared";
import { getStaged, sameValue, stageEdit, unstageEdit } from "./stagedStore";

/**
 * A name's intent (manage's `positions.<SYMBOL>.intent`), staged for Config like any other edit
 * (2026-11-12). The config editor splices only values that already exist, so a new name cannot be
 * written at `/positions/<SYM>/intent`: the whole `positions` object is staged instead, every other
 * name's overrides (disable, note) carried as the file has them. Saving still goes through Config ›
 * Position rules, the console's one write path; the wheel tab only stages.
 */

export const POSITIONS = "/positions";

/** The intents a person may choose. `no_wheel` takes a name off the wheel tab's list and keeps the
 *  put's stop; `pmcc` and `diagonal` are the PMCC view's. */
export const INTENTS = ["wheel", "no_wheel", "pmcc", "diagonal"] as const;
export type Intent = (typeof INTENTS)[number];

export const INTENT_WORDS: Record<Intent, string> = {
  wheel: "wheel",
  no_wheel: "not a wheel",
  pmcc: "pmcc",
  diagonal: "diagonal",
};

type Positions = Record<string, Record<string, unknown>>;

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** `positions` as the manage config file has it; null when the file or the block is missing. */
export function savedPositions(model: ConfigModelPayload | undefined): Positions | null {
  const doc = model?.targets.manage?.doc;
  if (!model?.targets.manage?.exists || !isObject(doc) || !isObject(doc["positions"])) return null;
  return doc["positions"] as Positions;
}

/** `positions` with any staged change over it. */
export function effectivePositions(model: ConfigModelPayload | undefined): Positions | null {
  const saved = savedPositions(model);
  if (saved === null) return null;
  const staged = getStaged("manage", POSITIONS);
  return staged.has && isObject(staged.value) ? (staged.value as Positions) : saved;
}

/** A name's intent, staged or saved, and whether the staged one is unsaved. */
export function intentOf(model: ConfigModelPayload | undefined, symbol: string): { intent: string | null; unsaved: boolean } {
  const saved = savedPositions(model);
  const eff = effectivePositions(model);
  const read = (p: Positions | null) => {
    const v = p?.[symbol]?.["intent"];
    return typeof v === "string" ? v : null;
  };
  const now = read(eff);
  return { intent: now, unsaved: now !== read(saved) };
}

/** Stage a name's intent (null clears it). Returns why it cannot, when the file has no `positions`. */
export function stageIntent(model: ConfigModelPayload | undefined, symbol: string, intent: string | null): string | null {
  const saved = savedPositions(model);
  const eff = effectivePositions(model);
  if (saved === null || eff === null) return "manage.json has no positions block to add the intent to";
  const next: Positions = JSON.parse(JSON.stringify(eff)) as Positions;
  const entry: Record<string, unknown> = { ...(next[symbol] ?? {}) };
  if (intent === null) delete entry["intent"];
  else entry["intent"] = intent;
  if (Object.keys(entry).length === 0) delete next[symbol];
  else next[symbol] = entry;
  if (sameValue(next, saved)) unstageEdit("manage", POSITIONS);
  else stageEdit("manage", POSITIONS, next);
  return null;
}

/** The names whose intent differs between the staged and the saved `positions`. */
export function pendingIntents(model: ConfigModelPayload | undefined): Array<{ symbol: string; from: string | null; to: string | null }> {
  const saved = savedPositions(model) ?? {};
  const eff = effectivePositions(model) ?? {};
  const names = new Set([...Object.keys(saved), ...Object.keys(eff)]);
  const out: Array<{ symbol: string; from: string | null; to: string | null }> = [];
  for (const s of [...names].sort()) {
    const from = typeof saved[s]?.["intent"] === "string" ? (saved[s]!["intent"] as string) : null;
    const to = typeof eff[s]?.["intent"] === "string" ? (eff[s]!["intent"] as string) : null;
    if (from !== to) out.push({ symbol: s, from, to });
  }
  return out;
}

/** What a stored intent reads as. */
export function intentWords(intent: string | null): string {
  if (intent === null) return "none";
  return (INTENT_WORDS as Record<string, string>)[intent] ?? intent;
}
