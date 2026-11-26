import type { ConfigModelPayload } from "@console/shared";
import { clearStaged, getStaged, sameValue, stageEdit, unstageEdit } from "./stagedStore";

/**
 * A name's intent, staged for Config like any other edit: manage's `positions.<SYMBOL>.intent`, for every
 * trade on the name it can describe. The config editor splices only values that already exist, so a new
 * key cannot be written at `/positions/<SYM>/intent`: the whole `positions` object is staged instead, every
 * other name's overrides carried as the file has them. Saving goes through Config › Position rules, the
 * console's general write path.
 *
 * A trade's own intent (`trades.<ID>.intent`, or `trades.<long contract>.intent` for a PMCC) is not staged
 * here: it is answered on the position (Live › positions, the card's strip and the trade's Intent tab)
 * through its own narrow save. This module only reads `trades`, for Config › Intents' list.
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

/** The intent a block (`positions` or `trades`) holds for one key, or null. */
const intentIn = (block: Positions | null, key: string): string | null => {
  const v = block?.[key]?.["intent"];
  return typeof v === "string" ? v : null;
};

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
  const now = intentIn(effectivePositions(model), symbol);
  return {
    intent: now,
    unsaved: now !== intentIn(savedPositions(model), symbol),
  };
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
    const from = intentIn(saved, s);
    const to = intentIn(eff, s);
    if (from !== to) out.push({ symbol: s, from, to });
  }
  return out;
}

/** What a stored intent reads as. */
export function intentWords(intent: string | null): string {
  if (intent === null) return "none";
  return (INTENT_WORDS as Record<string, string>)[intent] ?? intent;
}

/** Each trade that holds an intent in the manage config file (`trades`), by its key (a trade ID, or a PMCC's
 *  long contract), for Config › Intents to list. Read only: a trade's intent is answered on the position.
 *  Empty when the file has no `trades` block or no file. */
export function tradeIntents(model: ConfigModelPayload | undefined): Array<{ key: string; intent: string }> {
  const doc = model?.targets.manage?.doc;
  if (!model?.targets.manage?.exists || !isObject(doc) || !isObject(doc["trades"])) return [];
  const trades = doc["trades"] as Positions;
  return Object.keys(trades)
    .filter((k) => !k.startsWith("_"))
    .flatMap((k) => {
      const intent = intentIn(trades, k);
      return intent === null ? [] : [{ key: k, intent }];
    })
    .sort((a, b) => a.key.localeCompare(b.key));
}

/** Drop every staged intent. */
export function discardIntents(): void {
  clearStaged([{ target: "manage", pointer: POSITIONS }]);
}

/** What a save writes: the staged `positions` whole, and nothing that was not staged. */
export function intentEdits(model: ConfigModelPayload | undefined): Array<{ pointer: string; value: unknown }> {
  return getStaged("manage", POSITIONS).has ? [{ pointer: POSITIONS, value: effectivePositions(model) }] : [];
}
