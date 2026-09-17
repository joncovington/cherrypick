import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { hasTable, readOnlyDb, str } from "./db.js";

/**
 * Which experiment an `advised:*` ledger tag belongs to, and which base it shadows.
 *
 * Since 2026-09-17 a module runs any number of advisor experiments at once and each writes ITS
 * OWN book, tagged `advised:<experiment name>` (earnings: `advised:<name>:<strategy>`). The tag
 * no longer names the base; the base sits on the advisor's `experiments` row (`base_profile`),
 * beside a `tag` column that the advisor stamps as it creates the row. So every console surface
 * that meets an advised tag resolves it HERE, in one order, rather than stripping a prefix:
 *
 *  1. the experiment id stamped on the rows (`core.metrics` groups stamped rows as
 *     `<tag>@<experiment id>`) — the ledger said which experiment wrote them;
 *  2. the tag itself, against each experiment's `tag` (or `advised:` + slug(name) on a store
 *     whose `experiments` table predates the column);
 *  3. failing both, the legacy reading: `advised:<base>` written before the change, or after it
 *     by a row no experiment could be attributed to — paired against the base the tag names.
 *
 * `slugExperimentName` mirrors `cherrypick.core.advice.slug` exactly (lowercase, runs of anything
 * but [a-z0-9] collapsed to one dash, trimmed): the two must agree or a store without the `tag`
 * column resolves nothing. It is the one derivation in this file, and it is only a fallback.
 */

export const ADVISED_PREFIX = "advised:";

export function slugExperimentName(name: unknown): string {
  return String(name ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** `advised:<slug(name)>`, or `advised:<slug(name)>:<strategy>` for a per-strategy twin. */
export function advisedTagFor(name: unknown, strategy: string | null = null): string {
  const tag = `${ADVISED_PREFIX}${slugExperimentName(name)}`;
  return strategy ? `${tag}:${strategy}` : tag;
}

export interface ExperimentRef {
  id: string;
  module: string;
  baseProfile: string;
  name: string | null;
  /** The book this experiment writes: the row's `tag` column, else derived from its name; null
   *  when the row carries neither (a legacy row that only ever wrote `advised:<base>`). */
  tag: string | null;
  /** queued | active | expired | killed */
  status: string;
  /** The stored verdict's own `underpowered`, or null when no verdict has been computed. */
  underpowered: boolean | null;
}

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.advisorDir, "advisor.db");
}

function parseUnderpowered(raw: unknown): boolean | null {
  if (typeof raw !== "string" || raw === "") return null;
  try {
    const parsed = JSON.parse(raw) as { underpowered?: unknown };
    return parsed.underpowered === true ? true : parsed.underpowered === false ? false : null;
  } catch {
    return null;
  }
}

/** The advisor row's own tag when the column exists and is set; else derived from the name. */
export function tagOfRow(r: Record<string, unknown>): string | null {
  const stored = str(r["tag"]);
  if (stored !== null && stored !== "") return stored;
  const name = str(r["name"]);
  if (name === null || slugExperimentName(name) === "") return null;
  return advisedTagFor(name);
}

/**
 * Every experiment the advisor holds for one module (all statuses), or null when there is no
 * store or no `experiments` table to ask — a real, ordinary state on a fresh machine, and one the
 * callers treat differently from "asked, and found nothing" (a legacy tag on a machine with no
 * advisor at all keeps the pre-change reading; the same tag beside a populated store is history).
 */
export function readExperimentIndex(config: ConsoleConfig, module: string): ExperimentRef[] | null {
  const read = readOnlyDb<ExperimentRef[] | null>(dbPath(config), (db) => {
    if (!hasTable(db, "experiments")) return null;
    return db
      .prepare<[string], Record<string, unknown>>(
        "SELECT * FROM experiments WHERE module = ? ORDER BY created_at, id",
      )
      .all(module)
      .map((r) => ({
        id: String(r["id"]),
        module: String(r["module"]),
        baseProfile: String(r["base_profile"] ?? ""),
        name: str(r["name"]),
        tag: tagOfRow(r),
        status: String(r["status"] ?? ""),
        underpowered: parseUnderpowered(r["verdict_json"]),
      }));
  });
  return read.status === "ok" ? read.value : null;
}

/** An `advised:` tag taken apart: `advised:<head>[:<strategy>][@<experiment id>]`. The head is
 *  either an experiment's slug or, legacy, a base name; neither ever carries a colon, so the first
 *  colon after the prefix starts earnings' strategy suffix. */
export function splitAdvisedTag(tag: string): { head: string; strategy: string | null; stamped: string | null } {
  const rest = tag.startsWith(ADVISED_PREFIX) ? tag.slice(ADVISED_PREFIX.length) : tag;
  const at = rest.indexOf("@");
  const body = at === -1 ? rest : rest.slice(0, at);
  const stamped = at === -1 ? null : rest.slice(at + 1) || null;
  const colon = body.indexOf(":");
  if (colon === -1) return { head: body, strategy: null, stamped };
  return { head: body.slice(0, colon), strategy: body.slice(colon + 1) || null, stamped };
}

export interface ResolvedAdvisedTag {
  /** The book this advised tag is measured against. */
  base: string;
  experiment: ExperimentRef | null;
  /** How the experiment was found: the rows' stamp, the tag itself, or not at all (legacy). */
  attribution: "stamp" | "tag" | "none";
  /** The stamp on the rows even when no advisor row matched it — the ledger's own attribution. */
  stampedId: string | null;
  strategy: string | null;
}

/**
 * Resolve one advised tag through the order in the file header. `index` null means "no store to
 * ask"; the result is then the legacy reading with no experiment, which is all a prefix could say.
 */
export function resolveAdvisedTag(tag: string, index: ExperimentRef[] | null): ResolvedAdvisedTag {
  const { head, strategy, stamped } = splitAdvisedTag(tag);
  const suffix = strategy === null ? "" : `:${strategy}`;
  const rows = index ?? [];

  const byStamp = stamped === null ? undefined : rows.find((e) => e.id === stamped);
  if (byStamp !== undefined) {
    return { base: byStamp.baseProfile + suffix, experiment: byStamp, attribution: "stamp", stampedId: stamped, strategy };
  }
  const bare = `${ADVISED_PREFIX}${head}`;
  const byTag = rows.find((e) => e.tag === bare);
  if (byTag !== undefined) {
    return { base: byTag.baseProfile + suffix, experiment: byTag, attribution: "tag", stampedId: stamped, strategy };
  }
  return { base: head + suffix, experiment: null, attribution: "none", stampedId: stamped, strategy };
}
