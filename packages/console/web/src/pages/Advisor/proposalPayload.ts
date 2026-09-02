/**
 * Reading a stored proposal payload.
 *
 * A proposal is stored as the model's RAW reply object (`experiments.record_checkpoint` keeps
 * `raw`, not the typed form), so this page renders something `packages/advisor` has already parsed
 * once and did not keep. That makes two things true, and both shape this module:
 *
 * - **The shapes have to be recognised again here.** `proposals._as_params` accepts three, and a
 *   payload this file cannot read renders as a proposal that never says what it would change.
 * - **Only the reading is shared.** Admission was decided against the module's declared bounds,
 *   by `cherrypick.core.advice`, long before anything reached the console. Nothing here re-decides
 *   it, and nothing here is consulted by anything that does.
 *
 * Kept out of the page component so the shape-reading — the part that mirrors Python and so the
 * part that can drift away from it — can be tested without mounting anything.
 */

/**
 * What a proposal card renders on its own. Everything else in a payload is shown verbatim rather
 * than dropped: the taxonomy lives in `proposals.py` and can gain a field without this file hearing
 * about it, so the handled keys are listed in one place precisely so the leftovers can be found.
 */
export const RENDERED_KEYS = new Set([
  "kind", "module", "raw",
  "title", "text", "hypothesis", "rationale", "success_metric",
  "name", "experiment_id", "recommendation", "sessions",
  "params", "spec_json",
]);

export type ParamRow = { param: string; value: unknown; rationale: string | null };

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A payload value as text, without `[object Object]` for the nested ones. */
export function scalar(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/**
 * The three shapes a `params` payload arrives in, flattened to rows: the list of `{param, value}`
 * the prompt asks for, the plain `{param: value}` map a model reaches for anyway, and one bare
 * entry sent without its enclosing list.
 *
 * The bare entry is told from the map the same way `proposals._is_single_entry` tells them apart —
 * on a string `param` key. That is decidable rather than preferred: reading it as a map would
 * require a module to have declared a bound on a key called `param`, and a bound is a strategy
 * parameter name.
 *
 * `null` means there is no params field to render at all, which is not the same as an empty one.
 */
export function paramRows(value: unknown): ParamRow[] | null {
  if (Array.isArray(value)) {
    return value.filter(isRecord).map((row) => ({
      param: scalar(row["param"]),
      value: row["value"],
      rationale: typeof row["rationale"] === "string" ? row["rationale"] : null,
    }));
  }
  if (!isRecord(value)) return null;
  if (typeof value["param"] === "string" && value["param"] !== "") {
    return [{
      param: value["param"],
      value: value["value"],
      rationale: typeof value["rationale"] === "string" ? value["rationale"] : null,
    }];
  }
  return Object.entries(value).map(([param, v]) => ({ param, value: v, rationale: null }));
}

/**
 * The payload entries no card field covers.
 *
 * A `malformed` proposal is what makes this load-bearing rather than tidy: its payload is whatever
 * the model sent, recorded precisely because it could not be typed, so none of the rendered keys
 * will match and without this the card is a reject reason over an empty body. It need not even be
 * an object — `null` says so, and the caller shows the thing whole rather than walking it, since
 * `Object.entries` on a string is a table of its characters.
 */
export function otherFields(payload: unknown): Array<[string, unknown]> | null {
  if (!isRecord(payload)) return null;
  return Object.entries(payload).filter(([k]) => !RENDERED_KEYS.has(k));
}
