import type { ConfigTargetId, ConfigTargetModel } from "@console/shared";

/**
 * What this page is willing to edit.
 *
 * The suite has no JSON schema anywhere, so this map IS the form's schema — and being an allow-list
 * is the point rather than a limitation. The configs hold hundreds of keys; almost all of them are
 * decided once and then left alone, and putting a settlement time or a database path one careless
 * click from a live loop earns nothing. What is here is what actually changes between sessions:
 * which experiment arms are running, which modules and symbols, the entry windows, where alerts go.
 *
 * Everything else stays where it already works — a text editor, with the config's own notes in view.
 */

export type FieldSection = "arms" | "modules" | "timing" | "notify" | "rules";
export type FieldType = "boolean" | "number" | "string" | "time" | "stringList" | "enum";

export interface FieldMeta {
  target: ConfigTargetId;
  /** A fixed pointer, or omitted when `dynamic` enumerates them from the document. */
  pointer?: string;
  /**
   * Enumerate the keys of the object at `under`, editing `${under}/${key}${child}` for each.
   * `under` may list several pointers: the first one the document actually has is used. An arm
   * registry is spelled `arms`, `books` or `profiles` depending on the module and on how long ago
   * the operator wrote the file, and all three are read for good (see cherrypick.core.config).
   * A single pointer here would not error on the others -- the toggles would just silently not be
   * offered, which reads as a module that has no arms.
   */
  dynamic?: { under: string | string[]; child: string };
  label: string;
  help?: string;
  type: FieldType;
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  section: FieldSection;
}

export interface SectionMeta {
  id: FieldSection;
  title: string;
  blurb: string;
}

export const SECTIONS: SectionMeta[] = [
  {
    // "Arms" alone (2026-10-30): `profile` is a word the suite keeps for other things; MEIC's
    // registry is still spelled `profiles` in its file, and the toggle's own label says so.
    id: "arms",
    title: "Arms",
    blurb: "Which experiments run each session. Every enabled arm is evaluated on every tick.",
  },
  {
    id: "modules",
    title: "Modules & symbols",
    blurb: "Which modules the supervisor drives, and what each of them trades.",
  },
  {
    id: "timing",
    title: "Timing & cadence",
    blurb: "Entry windows and loop pacing. A change lands on the next supervisor pass — no reinstall.",
  },
  // "Alerts" (2026-10-30): "jobs" already means the supervisor's jobs and the research queue's.
  { id: "notify", title: "Alerts", blurb: "Where alerts go, and how often the scheduled checks run." },
  {
    id: "rules",
    title: "Position rules",
    blurb:
      "The daily position evaluation (Live → positions and portfolio): when it runs, and the thresholds it judges the live " +
      "account by. Advice only — it never places an order. A change applies at the next evaluation.",
  },
];

export const FIELDS: FieldMeta[] = [
  // --- arms & profiles ---------------------------------------------------------------------
  {
    target: "flies",
    dynamic: { under: ["/arms", "/books", "/profiles"], child: "/enabled" },
    label: "Flies arm",
    type: "boolean",
    section: "arms",
  },
  {
    target: "meic-risk",
    dynamic: { under: ["/profiles", "/arms", "/books"], child: "/enabled" },
    label: "MEIC risk profile",
    type: "boolean",
    section: "arms",
    help: "Every enabled profile is evaluated each tick — this switch, not active_profile, is what runs.",
  },

  // --- modules & symbols -------------------------------------------------------------------
  {
    target: "orchestrator",
    dynamic: { under: "/modules", child: "/enabled" },
    label: "Module",
    type: "boolean",
    section: "modules",
    help: "Off means the supervisor stops driving it entirely — no paper loop, no scheduled jobs.",
  },
  { target: "meic", pointer: "/symbols", label: "MEIC symbols", type: "stringList", section: "modules" },
  { target: "flies", pointer: "/symbols", label: "Flies symbols", type: "stringList", section: "modules" },
  { target: "gex", pointer: "/symbols", label: "GEX symbols", type: "stringList", section: "modules" },
  {
    target: "streamer",
    pointer: "/symbols",
    label: "Streamer symbols",
    type: "stringList",
    section: "modules",
    help: "Normally empty — the streamer subscribes to the union of every module's stream request.",
  },

  // --- timing & cadence --------------------------------------------------------------------
  { target: "meic", pointer: "/entry_window_start", label: "MEIC entry opens", type: "time", section: "timing" },
  { target: "meic", pointer: "/entry_window_end", label: "MEIC entry closes", type: "time", section: "timing" },
  {
    target: "meic",
    pointer: "/daily_ic_trade_target",
    label: "MEIC daily IC target",
    type: "number",
    min: 0,
    section: "timing",
  },
  {
    target: "meic",
    pointer: "/max_concurrent_ics",
    label: "MEIC max concurrent ICs",
    type: "number",
    min: 1,
    section: "timing",
  },
  { target: "flies", pointer: "/defaults/no_entry_before", label: "Flies no entry before", type: "time", section: "timing" },
  {
    target: "flies",
    pointer: "/defaults/min_seconds_between_entries",
    label: "Flies min seconds between entries",
    type: "number",
    min: 0,
    step: 30,
    section: "timing",
  },
  {
    target: "flies",
    pointer: "/defaults/max_positions",
    label: "Flies max positions",
    type: "number",
    min: 1,
    section: "timing",
  },

  // --- notifications & jobs ----------------------------------------------------------------
  {
    target: "orchestrator",
    pointer: "/notify/channels",
    label: "Alert channels",
    type: "stringList",
    section: "notify",
    help: "Webhook URLs live in the keyring, never here — this is only which channels are used.",
  },
  { target: "orchestrator", pointer: "/notify/trade_channels", label: "Trade channels", type: "stringList", section: "notify" },
  {
    target: "orchestrator",
    pointer: "/notify/trade_summary/mode",
    label: "Trade summary mode",
    type: "enum",
    options: ["summary", "each", "off"],
    section: "notify",
  },
  {
    target: "orchestrator",
    pointer: "/trade_notify/interval_seconds",
    label: "Trade notify interval",
    type: "number",
    min: 5,
    step: 5,
    section: "notify",
  },
  {
    target: "orchestrator",
    pointer: "/watchdog/interval_minutes",
    label: "Watchdog interval",
    type: "number",
    min: 1,
    max: 60,
    section: "notify",
  },
  {
    target: "orchestrator",
    pointer: "/watchdog/renotify_minutes",
    label: "Watchdog re-notify",
    type: "number",
    min: 5,
    section: "notify",
  },
  { target: "orchestrator", pointer: "/reconcile/schedule/enabled", label: "Daily reconcile job", type: "boolean", section: "notify" },
  { target: "orchestrator", pointer: "/review/narrative", label: "Review narrative", type: "boolean", section: "notify" },
  // The QuikOptions series can be paused, retitled and re-routed here; `enabled` stays out, because
  // turning the capture on needs a person to sign in to the site, which this page cannot do. The
  // orchestrator's config editor refuses a title, webhook or section the post script would.
  {
    target: "orchestrator",
    pointer: "/quikoptions/post",
    label: "Options flow Discord series",
    type: "boolean",
    section: "notify",
    help: "The daily QuikOptions posts after the close. Needs the capture (quikoptions.enabled) on.",
  },
  {
    target: "orchestrator",
    pointer: "/quikoptions/post_title",
    label: "Options flow series title",
    type: "string",
    section: "notify",
    help: "One line, at most 80 characters; the session date is added after it.",
  },
  {
    target: "orchestrator",
    pointer: "/quikoptions/post_webhook",
    label: "Options flow webhook",
    type: "enum",
    options: ["notify", "reporting"],
    section: "notify",
    help:
      "The suite's Discord notify webhook, or the reporting channel (discord_reporting in the keyring). Never a fallback. " +
      "The weekly scorecard always goes to notify.",
  },
  { target: "orchestrator", pointer: "/quikoptions/post_morning", label: "Options flow morning post", type: "boolean", section: "notify" },
  { target: "orchestrator", pointer: "/quikoptions/post_weekly", label: "Options flow weekly scorecard", type: "boolean", section: "notify" },
  {
    target: "orchestrator",
    pointer: "/config_backup/enabled",
    label: "Settings history (git)",
    type: "boolean",
    section: "notify",
    help:
      "Commits your config files when they change (never data or passwords) and pushes them if a remote " +
      "is set. Needs a one-time `run.py config-backup --init`; doctor says if it is not set up.",
  },
  {
    target: "orchestrator",
    pointer: "/config_backup/interval_minutes",
    label: "Settings history interval",
    type: "number",
    min: 1,
    max: 1440,
    section: "notify",
  },
  {
    target: "orchestrator",
    pointer: "/config_backup/push",
    label: "Push settings history",
    type: "boolean",
    section: "notify",
    help: "Push each commit to the repository's remote. Use a PRIVATE remote: the files hold your strategy settings.",
  },

  // --- position rules ----------------------------------------------------------------------
  {
    target: "orchestrator",
    pointer: "/manage/enabled",
    label: "Daily position evaluation",
    help: "Reads the live account read-only each trading day. The supervisor picks the change up on its next pass.",
    type: "boolean",
    section: "rules",
  },
  { target: "orchestrator", pointer: "/manage/at", label: "Evaluation time (ET)", type: "time", section: "rules" },
  { target: "orchestrator", pointer: "/manage/notify", label: "Notify when action is needed", type: "boolean", section: "rules" },
  {
    target: "manage",
    pointer: "/trade_grouping",
    label: "Trade grouping",
    help: "order_chain follows the broker's order chains (opening order and every roll) and uses their net cash as the basis; expiry_chain and underlying price the open legs only.",
    type: "enum",
    options: ["order_chain", "expiry_chain", "underlying"],
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/credit/take_profit/capture",
    label: "Credit: take profit at share of credit",
    type: "number",
    min: 0.05,
    max: 1,
    step: 0.05,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/credit/stop/loss_multiple_of_credit",
    label: "Credit: stop at loss × credit",
    type: "number",
    min: 0.25,
    max: 5,
    step: 0.25,
    section: "rules",
  },
  { target: "manage", pointer: "/credit/time_exit/dte", label: "Credit: close at DTE", type: "number", min: 0, max: 60, section: "rules" },
  {
    target: "manage",
    pointer: "/credit/close_before_earnings/enabled",
    label: "Credit: close before earnings",
    type: "boolean",
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/debit/stop/loss_fraction",
    label: "Debit: stop at loss of debit",
    type: "number",
    min: 0.1,
    max: 1,
    step: 0.05,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/debit/take_profit/gain_fraction",
    label: "Debit: take profit at gain on debit",
    type: "number",
    min: 0.1,
    max: 5,
    step: 0.05,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/legs/short_take_profit/capture",
    label: "Short leg: buy back at share of premium",
    type: "number",
    min: 0.5,
    max: 1,
    step: 0.05,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/expiry/close_by_dte",
    label: "Close physically-settled legs at DTE",
    type: "number",
    min: 0,
    max: 14,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/risk/max_risk_pct",
    label: "Per-trade risk budget (share of net liq)",
    type: "number",
    min: 0.005,
    max: 0.2,
    step: 0.005,
    section: "rules",
  },

  {
    target: "manage",
    pointer: "/roll/naked_put/enabled",
    label: "Naked short puts: roll out for a credit instead of closing",
    help: "When a rule says to close a naked short put, offer a same-strike roll to the next monthly 4+ weeks out if it is modelled to take in a credit.",
    type: "boolean",
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/roll/naked_put/min_credit",
    label: "Naked put roll: minimum credit per share",
    type: "number",
    min: 0,
    max: 5,
    step: 0.05,
    section: "rules",
  },

  {
    target: "manage",
    pointer: "/roll/diagonal_short/enabled",
    label: "Diagonals: roll the short side out for a credit",
    help: "At the short's 21-day exit or 80% captured, roll the shorts (and their wings) to the next monthly at the same strikes, if the trade is not stopped, the long outlives the new short, and the short stays beyond the long's breakeven.",
    type: "boolean",
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/roll/diagonal_short/min_long_days",
    label: "Diagonal roll: long must outlive the new short by (days)",
    type: "number",
    min: 0,
    max: 365,
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/directional/enabled",
    label: "Judge near-even-money trades on their risk",
    help: "Super bears and bulls and risk reversals opened for a premium under the share below of their max loss are judged on max gain and max loss, not a percentage of the premium.",
    type: "boolean",
    section: "rules",
  },
  {
    target: "manage",
    pointer: "/directional/max_premium_share",
    label: "Near even money: premium under this share of max loss",
    type: "number",
    min: 0,
    max: 1,
    step: 0.05,
    section: "rules",
  },

  // --- the console's own pacing (the `dev` section until 2026-10-30, its only field) ---------
  {
    target: "orchestrator",
    pointer: "/console/dev_backoff_seconds",
    label: "Console crash backoff",
    type: "number",
    min: 1,
    section: "timing",
  },
];

/** The value at a JSON pointer in a parsed document, or `undefined` when the path is absent. */
export function valueAt(doc: Record<string, unknown> | null, pointer: string): unknown {
  if (doc === null) return undefined;
  let cur: unknown = doc;
  for (const raw of pointer.slice(1).split("/")) {
    const tok = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (typeof cur === "object" && cur !== null && !Array.isArray(cur) && tok in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[tok];
    } else if (Array.isArray(cur)) {
      const n = Number(tok);
      if (!Number.isInteger(n) || n < 0 || n >= cur.length) return undefined;
      cur = cur[n];
    } else {
      return undefined;
    }
  }
  return cur;
}

export interface ResolvedField {
  meta: FieldMeta;
  pointer: string;
  label: string;
  value: unknown;
  help: string | null;
  guardedHint: string | null;
}

/**
 * The explanation for a field, curated first and the config's own note second.
 *
 * The suite writes its documentation INTO the configs (`_comment` / `_note` beside the key they
 * describe), so most fields already have a good sentence attached — and reading it from the file
 * means the page cannot drift from what the file says. A curated `help` wins where the raw note is
 * too terse, too internal, or absent.
 */
export function resolveHelp(meta: FieldMeta, doc: Record<string, unknown> | null, pointer: string): string | null {
  if (meta.help !== undefined) return meta.help;
  const lastSlash = pointer.lastIndexOf("/");
  const parentPtr = pointer.slice(0, lastSlash);
  const leaf = pointer.slice(lastSlash + 1);
  const parent = parentPtr === "" ? doc : valueAt(doc, parentPtr);
  if (typeof parent !== "object" || parent === null || Array.isArray(parent)) return null;
  const siblings = parent as Record<string, unknown>;
  for (const key of [`_${leaf}_note`, `_${leaf}_comment`, "_note", "_comment"]) {
    const note = siblings[key];
    if (typeof note === "string" && note.trim() !== "") return note;
  }
  return null;
}

/**
 * Expand the metadata for one section into the concrete fields a document actually has. A field
 * whose pointer is absent from the file is dropped rather than rendered empty — the alternative is
 * offering to create keys a module never reads.
 */
export function resolveSection(
  section: FieldSection,
  targets: Record<ConfigTargetId, ConfigTargetModel> | undefined,
): Array<{ target: ConfigTargetId; fields: ResolvedField[] }> {
  if (targets === undefined) return [];
  const out: Array<{ target: ConfigTargetId; fields: ResolvedField[] }> = [];
  for (const meta of FIELDS.filter((f) => f.section === section)) {
    const model = targets[meta.target];
    if (model === undefined || !model.exists || model.doc === null) continue;
    const guardedBy = new Map(model.guarded.map((g) => [g.pointer, g.hint]));

    const pointers: Array<{ pointer: string; label: string }> = [];
    if (meta.dynamic !== undefined) {
      const candidates = Array.isArray(meta.dynamic.under) ? meta.dynamic.under : [meta.dynamic.under];
      const under = candidates.find((p) => {
        const v = valueAt(model.doc, p);
        return typeof v === "object" && v !== null && !Array.isArray(v);
      });
      if (under === undefined) continue;
      const container = valueAt(model.doc, under) as Record<string, unknown>;
      for (const key of Object.keys(container)) {
        // `_`-prefixed keys are the configs' docs-as-data, not entries.
        if (key.startsWith("_")) continue;
        pointers.push({ pointer: `${under}/${key}${meta.dynamic.child}`, label: key });
      }
    } else if (meta.pointer !== undefined) {
      pointers.push({ pointer: meta.pointer, label: meta.label });
    }

    const fields: ResolvedField[] = [];
    for (const { pointer, label } of pointers) {
      const value = valueAt(model.doc, pointer);
      if (value === undefined) continue;
      fields.push({
        meta,
        pointer,
        label,
        value,
        help: resolveHelp(meta, model.doc, pointer),
        guardedHint: guardedBy.get(pointer) ?? null,
      });
    }
    if (fields.length === 0) continue;

    const existing = out.find((g) => g.target === meta.target);
    if (existing === undefined) out.push({ target: meta.target, fields });
    else existing.fields.push(...fields);
  }
  return out;
}

export const TARGET_TITLES: Record<ConfigTargetId, string> = {
  orchestrator: "suite",
  meic: "meic",
  flies: "flies",
  gex: "gex",
  earnings: "earnings",
  streamer: "streamer",
  "meic-risk": "meic risk profiles",
  manage: "position rules",
};
