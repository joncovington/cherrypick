import { useCallback, useSyncExternalStore } from "react";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { mutateJson } from "../../lib/api";
import {
  IDLE,
  answerTrade,
  finished,
  saveIntent,
  starting,
  type StripState,
} from "./stripAnswer";

/**
 * What the console has saved for each trade's intent, shared by every place that asks (the card's strip
 * and the opened trade's Intent tab), so one never offers the question the other has just had answered.
 * A module-level store, the shape of `lib/toast.ts`: one entry a trade, stamped with the ask as manage last
 * read it (asked or not, and its current answer). An entry is read only while its stamp is the ask's now,
 * and `pruneIntentAnswers` drops it once manage has re-evaluated, so an answer already read never comes
 * back if the question later reopens, and the store holds no more than one entry per trade.
 */

interface Entry {
  stamp: string;
  state: StripState;
}

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();

const stampOf = (ask: ManageIntentAsk) => `${ask.asked}|${ask.current ?? ""}`;

function put(trade: string, ask: ManageIntentAsk, state: StripState): void {
  entries.set(trade, { stamp: stampOf(ask), state });
  for (const l of listeners) l();
}

/** The saved state for this trade under this ask: nothing unless it was saved under the ask as it stands. */
export function savedFor(trade: string, ask: ManageIntentAsk): StripState {
  const e = entries.get(trade);
  return e && e.stamp === stampOf(ask) ? e.state : IDLE;
}

/** Write one answer (or null, to clear) and record how it went. `post` is the console's mutation. */
export async function answer(
  trade: string,
  ask: ManageIntentAsk,
  intent: string | null,
  post: Parameters<typeof saveIntent>[2],
): Promise<void> {
  put(trade, ask, starting(savedFor(trade, ask)));
  const r = await saveIntent(trade, intent, post);
  put(trade, ask, finished(savedFor(trade, ask), r));
}

/** Drop what was saved under an ask that manage has since re-read: call with each evaluation's trades. */
export function pruneIntentAnswers(trades: ManageTrade[]): void {
  for (const t of trades) {
    const key = t.intentAsk ? answerTrade(t.intentAsk) : null;
    const e = key === null ? undefined : entries.get(key);
    if (key !== null && e && t.intentAsk && e.stamp !== stampOf(t.intentAsk))
      entries.delete(key);
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** The saved state for this trade's ask, and the one function that writes an answer. */
export function useIntentAnswer(
  trade: string,
  ask: ManageIntentAsk,
): [StripState, (intent: string | null) => Promise<void>] {
  const state = useSyncExternalStore(
    subscribe,
    () => savedFor(trade, ask),
    () => IDLE,
  );
  const send = useCallback(
    (intent: string | null) => answer(trade, ask, intent, mutateJson),
    [trade, ask],
  );
  return [state, send];
}
