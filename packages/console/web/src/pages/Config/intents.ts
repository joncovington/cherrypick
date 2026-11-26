import type { ConfigModelPayload, ManageIntentCandidate } from "@console/shared";
import { clearStaged, getStaged, sameValue, stageEdit, unstageEdit } from "./stagedStore";

/**
 * An intent, staged for Config like any other edit (2026-11-12). It is a fact about one trade: manage's
 * `trades.<trade ID>.intent` (or, for a PMCC, `trades.<long contract>.intent`), or a name's
 * `positions.<SYMBOL>.intent` for every trade on the name it can describe. The config editor splices
 * only values that already exist, so a new key cannot be written at `/positions/<SYM>/intent`: the whole
 * `positions` object is staged instead, and likewise the whole `trades` object, every other entry
 * (disable, note) carried as the file has them. A file written before `trades` existed has no such
 * block; the orchestrator's save adds it. Saving still goes through Config › Position rules, the
 * console's one write path; the wheel tab only stages.
 */

export const POSITIONS = "/positions";
export const TRADES = "/trades";

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
  return { intent: now, unsaved: now !== intentIn(savedPositions(model), symbol) };
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

// --- a trade's own intent (`trades`) ---------------------------------------------------------------

type Trades = Positions;

/** `trades` as the manage config file has it; `{}` when the file has none (an older copy: a save adds the
 *  block), null when there is no file to write. */
export function savedTrades(model: ConfigModelPayload | undefined): Trades | null {
  const doc = model?.targets.manage?.doc;
  if (!model?.targets.manage?.exists || !isObject(doc)) return null;
  return isObject(doc["trades"]) ? (doc["trades"] as Trades) : {};
}

/** `trades` with any staged change over it. */
export function effectiveTrades(model: ConfigModelPayload | undefined): Trades | null {
  const saved = savedTrades(model);
  if (saved === null) return null;
  const staged = getStaged("manage", TRADES);
  return staged.has && isObject(staged.value) ? (staged.value as Trades) : saved;
}

/** A trade's own intent, staged or saved, and whether the staged one is unsaved. `key` is its trade ID. */
export function tradeIntentOf(model: ConfigModelPayload | undefined, key: string): { intent: string | null; unsaved: boolean } {
  const now = intentIn(effectiveTrades(model), key);
  return { intent: now, unsaved: now !== intentIn(savedTrades(model), key) };
}

/** Stage a trade's own intent (null clears it, keeping any note or disable on the entry). Returns why it
 *  cannot, when there is no manage config file. */
export function stageTradeIntent(model: ConfigModelPayload | undefined, key: string, intent: string | null): string | null {
  const saved = savedTrades(model);
  const eff = effectiveTrades(model);
  if (saved === null || eff === null) return "there is no manage.json to add the intent to";
  const next: Trades = JSON.parse(JSON.stringify(eff)) as Trades;
  const entry: Record<string, unknown> = { ...(next[key] ?? {}) };
  if (intent === null) delete entry["intent"];
  else entry["intent"] = intent;
  if (Object.keys(entry).length === 0) delete next[key];
  else next[key] = entry;
  if (sameValue(next, saved)) unstageEdit("manage", TRADES);
  else stageEdit("manage", TRADES, next);
  return null;
}

/** The trades whose intent differs between the staged and the saved `trades`. */
export function pendingTradeIntents(model: ConfigModelPayload | undefined): Array<{ key: string; from: string | null; to: string | null }> {
  const saved = savedTrades(model) ?? {};
  const eff = effectiveTrades(model) ?? {};
  const out: Array<{ key: string; from: string | null; to: string | null }> = [];
  for (const k of [...new Set([...Object.keys(saved), ...Object.keys(eff)])].sort()) {
    const from = intentIn(saved, k);
    const to = intentIn(eff, k);
    if (from !== to) out.push({ key: k, from, to });
  }
  return out;
}

// --- a wheel candidate's answer -----------------------------------------------------------------------

/** The trade ID a candidate's answer is written under, or null when it is written under its name: manage
 *  keys it to the trade (`trades.<ID>.intent`) once the ID is stable, and to the name while it is not. */
export function candidateTradeKey(c: Pick<ManageIntentCandidate, "key" | "tradeId">): string | null {
  return c.tradeId !== null && c.key === `trades.${c.tradeId}.intent` ? c.tradeId : null;
}

/** A candidate keyed to its name while the name's own intent is for another trade (manage's `note`): the
 *  one place a name-level write would replace that intent, so it waits for a stable trade ID. */
const heldForId = (c: ManageIntentCandidate): boolean => candidateTradeKey(c) === null && c.note !== null;

const HELD_FOR_ID =
  "this trade has no stable ID yet, and the name's own intent is for another trade on it, so a name-level choice would replace that intent: choose once the next evaluation gives the trade an ID";

/** A candidate's answer, staged or saved. A trade keyed to itself is answered only by its own entry, and
 *  so is one held for an ID: the name's intent is for another trade on the name (manage lists the put
 *  because of it). */
export function candidateIntentOf(model: ConfigModelPayload | undefined, c: ManageIntentCandidate): { intent: string | null; unsaved: boolean } {
  const key = candidateTradeKey(c);
  if (heldForId(c)) return { intent: null, unsaved: false };
  return key !== null ? tradeIntentOf(model, key) : intentOf(model, c.underlying);
}

/** Stage a candidate's answer where manage keys it. Returns why it cannot. */
export function stageCandidateIntent(model: ConfigModelPayload | undefined, c: ManageIntentCandidate, intent: string | null): string | null {
  if (heldForId(c)) return HELD_FOR_ID;
  const key = candidateTradeKey(c);
  return key !== null ? stageTradeIntent(model, key, intent) : stageIntent(model, c.underlying, intent);
}

/** Drop every staged intent, names' and trades'. */
export function discardIntents(): void {
  clearStaged([
    { target: "manage", pointer: POSITIONS },
    { target: "manage", pointer: TRADES },
  ]);
}

/** What a save writes: each staged block whole, and nothing that was not staged. */
export function intentEdits(model: ConfigModelPayload | undefined): Array<{ pointer: string; value: unknown }> {
  const out: Array<{ pointer: string; value: unknown }> = [];
  if (getStaged("manage", POSITIONS).has) out.push({ pointer: POSITIONS, value: effectivePositions(model) });
  if (getStaged("manage", TRADES).has) out.push({ pointer: TRADES, value: effectiveTrades(model) });
  return out;
}
