import type { ManageIntentAsk } from "@console/shared";

/**
 * The strip on a position that asks what the trade is for (manage's `intent_ask`). These are its pure
 * parts: the words, and what to send. The console works out no shape rule and no option list; both come
 * from the ask, and a key the save route would refuse is never sent.
 */

export const INTENT_LABEL: Record<string, string> = {
  wheel: "Wheel",
  no_wheel: "Not a wheel",
  pmcc: "PMCC",
  diagonal: "Just a diagonal",
};

/** Why a put with no stable ID, whose name's intent is for another trade on it, is not answered on the name:
 * that answer would replace the name's intent. */
export const HELD_FOR_ID = "Its ID changes once its orders are known, and the name's intent is for another trade on it, so an answer on the name would replace that intent: wait for a stable ID.";

export const intentLabel = (intent: string): string =>
  INTENT_LABEL[intent] ?? intent;

export const QUESTION: Record<ManageIntentAsk["kind"], string> = {
  wheel: "Is this put a wheel?",
  pmcc: "Is this diagonal a PMCC?",
};

/** The trade key the save route takes for this ask (`trades.<key>.intent`), or null where it is not
 * answered on the position: a put whose ID is still provisional is answered on its name, in Config. */
export function answerTrade(ask: ManageIntentAsk): string | null {
  if (ask.scope === "name") return null;
  const m = /^trades\.(.+)\.intent$/.exec(ask.key);
  return m ? m[1]! : null;
}

/** What Undo writes back: null clears, an option puts it back, and undefined where the replaced value
 * is a free label the route will not write, so there is no Undo. */
export function restoreTo(
  previous: string | null,
  options: string[],
): string | null | undefined {
  if (previous === null) return null;
  return options.includes(previous) ? previous : undefined;
}

/** Why a save did not go through, from the route's status (`mutateJson` reports `HTTP <status>`). */
export function saveFailure(message: string): string {
  if (message.includes("HTTP 409"))
    return "The config changed on disk while you were here. Try again.";
  if (message.includes("HTTP 404"))
    return "manage has no readable config to write the answer to.";
  if (message.includes("HTTP 403"))
    return "The console refused the write. Reload the page and try again.";
  return "Not saved. Try again, or answer it on Config.";
}

/** What a strip is showing: the answer it saved (kept until manage reads it, so Undo is there even after a
 * failed Undo), whether a save is in flight, and why the last one failed. */
export interface StripState {
  saved: { intent: string; previous: string | null } | null;
  busy: boolean;
  why: string | null;
}

export const IDLE: StripState = { saved: null, busy: false, why: null };

export const starting = (s: StripState): StripState => ({
  ...s,
  busy: true,
  why: null,
});

/** The state after a save came back. A failure changes nothing but the reason, so an answer already saved
 * stays saved and can be undone again; an Undo that cleared the intent returns to the open question. */
export function finished(s: StripState, r: SaveResult): StripState {
  if (!r.ok) return { ...s, busy: false, why: r.why };
  if (r.intent === null) return IDLE;
  return {
    saved: { intent: r.intent, previous: r.previous },
    busy: false,
    why: null,
  };
}

export type SaveResult =
  | { ok: true; intent: string | null; previous: string | null }
  | { ok: false; why: string };

/** One answer, written through the save route. `post` is the console's CSRF-carrying mutation. The body
 * is the trade key and the intent and nothing else; the reply's `previous` is what Undo writes back. */
export async function saveIntent(
  trade: string,
  intent: string | null,
  post: (
    url: string,
    method: "POST",
    body: unknown,
  ) => Promise<{ intent: string | null; previous: string | null }>,
): Promise<SaveResult> {
  try {
    const r = await post("/api/config/intent", "POST", { trade, intent });
    return { ok: true, intent: r.intent, previous: r.previous };
  } catch (e) {
    return { ok: false, why: saveFailure(e instanceof Error ? e.message : "") };
  }
}
