import fs from "node:fs";
import path from "node:path";
import { obj } from "./db.js";

/**
 * packages/manage's `eval-<session>.json`, read once per change.
 *
 * The evaluation is about a megabyte and five readers want it (the Manage page, the Live page's
 * `today` and hand rows, the live marks, the time-spread account note and the Screeners fit), several
 * of them on a 15 s poll and some twice in one request. They share one parse, keyed on the file's
 * mtime and size, the way `logs.ts` and `skate.ts` stamp theirs: an unchanged file costs one `stat`.
 *
 * manage writes the file atomically (`write_json_atomic`: a temp file then a replace), so a reader
 * sees the old file or the new one, never half of either. A read that fails anyway (a replace racing
 * the open, a truncated or hand-edited file) is NOT cached: the entry is dropped and the next
 * request reads again.
 *
 * The returned object is shared, so it is deep-frozen when it is cached: a reader that tried to edit
 * it would throw in the test that covers it instead of changing what the next request sees. Readers
 * only read named keys and build new payloads from them.
 */

type Rec = Record<string, unknown>;

/** The sessions kept parsed at once: today's and the few the page can step back to. The bands history
 *  walks every session and does not come through here (`parseEvalDoc`). */
const KEEP = 4;

const parsed = new Map<string, { stamp: string; doc: Rec }>();

function freezeDeep<T>(v: T): T {
  if (v !== null && typeof v === "object" && !Object.isFrozen(v)) {
    Object.freeze(v);
    for (const child of Object.values(v as Rec)) freezeDeep(child);
  }
  return v;
}

export function evalFile(manageDir: string, session: string): string {
  return path.join(manageDir, `eval-${session}.json`);
}

/** The file's `mtime:size`, or null when it is not there. */
export function evalStamp(file: string): string | null {
  try {
    const st = fs.statSync(file);
    return `${st.mtimeMs}:${st.size}`;
  } catch {
    return null;
  }
}

/** One uncached parse: the object, or null when the file is missing or unreadable. */
export function parseEvalDoc(file: string): Rec | null {
  try {
    return obj(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

/** The session's evaluation, or null when there is none or it cannot be read. Read-only: the object is
 *  shared between requests and frozen. */
export function readEvalDoc(manageDir: string, session: string): Rec | null {
  const file = evalFile(manageDir, session);
  const stamp = evalStamp(file);
  if (stamp === null) {
    parsed.delete(file);
    return null;
  }
  const hit = parsed.get(file);
  if (hit !== undefined && hit.stamp === stamp) {
    // Most recently used last, so the oldest is the one that goes.
    parsed.delete(file);
    parsed.set(file, hit);
    return hit.doc;
  }
  // The stamp is taken before the read: a file replaced in between is cached under the older stamp and
  // read again next request, never kept stale.
  const doc = parseEvalDoc(file);
  if (doc === null) {
    parsed.delete(file);
    return null;
  }
  parsed.delete(file);
  parsed.set(file, { stamp, doc: freezeDeep(doc) });
  while (parsed.size > KEEP) {
    const oldest = parsed.keys().next().value;
    if (oldest === undefined) break;
    parsed.delete(oldest);
  }
  return doc;
}

/** Drop everything cached (tests). */
export function clearEvalDocs(): void {
  parsed.clear();
}
