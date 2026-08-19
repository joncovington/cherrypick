import fs from "node:fs";
import path from "node:path";
import type {
  ExperimentGuide,
  ExperimentGuideEntry,
  GuideNote,
  GuideOverride,
  TradingMode,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, str } from "./db.js";
import { adviceDeclOf, type AdviceDecl } from "./adviceDecl.js";

/**
 * What each experiment arm (flies) or risk profile (MEIC) is, what makes it different, and when it
 * ran — assembled from the three places that already know, rather than from prose written a second
 * time here.
 *
 *  - The module's own config notes. The suite documents its configs in their own data (`_note`,
 *    `_history_note`), so these are the module describing itself, not the console describing it.
 *  - The settings, reduced to the ones that actually distinguish. A plain diff against the base
 *    listed `entry_windows` for eleven of flies' twelve arms: true, and useless, since it separates
 *    them from nothing.
 *  - The ledger, for when it actually traded — the config says what something IS, only the book says
 *    whether it ever ran.
 */

function collectNotes(block: Record<string, unknown>): GuideNote[] {
  const notes: GuideNote[] = [];
  for (const [k, v] of Object.entries(block)) {
    if (!k.startsWith("_") || typeof v !== "string" || v.trim() === "") continue;
    notes.push({ key: k.replace(/^_/, ""), text: v });
  }
  // `_note` is the definition and leads; the rest keep config order behind it.
  notes.sort((a, b) => Number(b.key === "note") - Number(a.key === "note"));
  return notes;
}

const SEP = "␟";

/**
 * The (key, value) pairs MOST siblings state identically — a house convention rather than a
 * distinguisher.
 *
 * Majority rather than unanimity, deliberately: one arm departing from the convention is precisely
 * what makes that departure interesting, and a unanimity test would let a single dissenter suppress
 * the convention for everybody else.
 */
function majorityValues(blocks: Array<[string, Record<string, unknown>]>): Map<string, string> {
  const shared = new Map<string, string>();
  if (blocks.length <= 2) return shared;
  const tally = new Map<string, number>();
  for (const [, b] of blocks) {
    for (const [k, v] of Object.entries(b)) {
      if (k.startsWith("_") || k === "enabled") continue;
      const id = `${k}${SEP}${JSON.stringify(v)}`;
      tally.set(id, (tally.get(id) ?? 0) + 1);
    }
  }
  for (const [id, n] of tally) {
    if (n * 2 <= blocks.length) continue;
    const cut = id.indexOf(SEP);
    shared.set(id.slice(0, cut), id.slice(cut + SEP.length));
  }
  return shared;
}

function buildOverrides(
  block: Record<string, unknown>,
  defaults: Record<string, unknown>,
  shared: Map<string, string>,
): GuideOverride[] {
  const out: GuideOverride[] = [];
  for (const [k, v] of Object.entries(block)) {
    if (k.startsWith("_") || k === "enabled") continue;
    const inDefaults = Object.prototype.hasOwnProperty.call(defaults, k);
    const encoded = JSON.stringify(v);
    out.push({
      key: k,
      value: v,
      fallback: inDefaults ? defaults[k] : null,
      inDefaults,
      // Kept and labelled rather than dropped: "width-5 sets wing_width 5, same as the default" is
      // the answer to why that arm shows no width, and silence would just look like a bug.
      matchesDefault: inDefaults && JSON.stringify(defaults[k]) === encoded,
      sharedByMostArms: shared.get(k) === encoded,
    });
  }
  return out;
}

function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

interface LedgerRow {
  first: string;
  last: string;
  n: number;
}

function ledgerSpans(dbPath: string, table: string, column: string): Map<string, LedgerRow> {
  return withReadOnlyDb<Map<string, LedgerRow>>(dbPath, new Map(), (db) => {
    const out = new Map<string, LedgerRow>();
    for (const r of db
      .prepare<[], Record<string, unknown>>(
        `SELECT ${column} AS k, MIN(trade_date) AS first, MAX(trade_date) AS last, COUNT(*) AS n
           FROM ${table} WHERE ${column} IS NOT NULL GROUP BY ${column}`,
      )
      .all()) {
      out.set(String(r["k"]), { first: str(r["first"]) ?? "", last: str(r["last"]) ?? "", n: Number(r["n"] ?? 0) });
    }
    return out;
  });
}

function measurementBreaks(dbPath: string): ExperimentGuide["breaks"] {
  return withReadOnlyDb<ExperimentGuide["breaks"]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        "SELECT break_date, scope, kind, reason FROM measurement_breaks ORDER BY break_date",
      )
      .all()
      .map((r) => ({
        date: str(r["break_date"]) ?? "",
        scope: str(r["scope"]) ?? "*",
        kind: str(r["kind"]) ?? "",
        reason: str(r["reason"]) ?? "",
      })),
  );
}

/** Running first, then finished, then gone-from-config; each group in the config's own order. */
function order(entries: ExperimentGuideEntry[]): ExperimentGuideEntry[] {
  const rank = (e: ExperimentGuideEntry) => (e.removed ? 2 : e.enabled ? 0 : 1);
  return entries.sort((a, b) => rank(a) - rank(b));
}

/**
 * Rows in the ledger that the config no longer mentions. Without these the guide silently omits
 * profiles the History tab still shows — MEIC's book carries four (`large-spx`, `small-xsp`, …)
 * that were removed from config months ago, and a reader meeting one has nowhere to look it up.
 */
function removedEntries(seen: Map<string, LedgerRow>, known: Set<string>): ExperimentGuideEntry[] {
  const out: ExperimentGuideEntry[] = [];
  for (const [name, row] of seen) {
    if (known.has(name)) continue;
    out.push({
      name,
      enabled: false,
      retired: false,
      removed: true,
      notes: [],
      overrides: [],
      derived: [],
      firstSession: row.first,
      lastSession: row.last,
      positions: row.n,
    });
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : 1));
}

/**
 * The advisor's synthetic books (`advised:<base>`), which exist only in the ledger. They are not
 * config entries — the paper loop conjures each one at session start from the module config's
 * `advice` block, overlaying the admitted advice on the base arm/profile's own definition — so
 * without this they land in removedEntries and read "gone from config" while actively trading.
 * A book whose base the advice block no longer points at (or with advice off) is retired, which
 * is the honest reading: it stopped receiving advice, and only its open positions wind down.
 */
function advisedEntries(seen: Map<string, LedgerRow>, decl: AdviceDecl | null, unit: string): ExperimentGuideEntry[] {
  const out: ExperimentGuideEntry[] = [];
  for (const [name, row] of seen) {
    if (!name.startsWith("advised:")) continue;
    const base = name.slice("advised:".length);
    const active = decl !== null && decl.enabled && decl.base === base;
    out.push({
      name,
      enabled: active,
      retired: !active,
      removed: false,
      notes: [
        {
          key: "note",
          text:
            `The advisor's synthetic book: the ${base} ${unit}'s own definition with the session's ` +
            `admitted advice overlaid, run beside the un-advised ${base} as its control. Declared by ` +
            `the module config's advice block rather than the ${unit} registry, which is why it has ` +
            `no settings of its own to list here.`,
        },
      ],
      overrides: [],
      derived: [{ label: "advised twin of", value: base, detail: "from the tag itself — advised:<base>" }],
      firstSession: row.first,
      lastSession: row.last,
      positions: row.n,
    });
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : 1));
}

// ---------------------------------------------------------------------------------------------
// flies — arms
// ---------------------------------------------------------------------------------------------

export function readFliesArmGuide(config: ConsoleConfig, mode: TradingMode): ExperimentGuide {
  const base: ExperimentGuide = {
    module: "flies",
    mode,
    unit: "arm",
    groupNotes: [],
    breaks: [],
    entries: [],
    configMissing: true,
  };
  const doc = readJson(config.paths.fliesConfig);
  if (doc === null) return base;

  const armsBlock = (doc["arms"] ?? {}) as Record<string, unknown>;
  const defaults = (doc["defaults"] ?? {}) as Record<string, unknown>;
  const dbPath = path.join(config.paths.fliesDir, mode === "live" ? "live_trades.db" : "paper_trades.db");
  const seen = ledgerSpans(dbPath, "fly_positions", "arm");

  const blocks = Object.entries(armsBlock).filter(
    ([n, v]) => !n.startsWith("_") && typeof v === "object" && v !== null,
  ) as Array<[string, Record<string, unknown>]>;
  const shared = majorityValues(blocks);

  const entries: ExperimentGuideEntry[] = blocks.map(([name, block]) => {
    const row = seen.get(name);
    const enabled = block["enabled"] === true;
    // Mirrors engine.select_center: `center_rule` if the arm sets one, else the arm's OWN NAME.
    // Without this the headline comparison is invisible — the `gex` arm carries no center_rule key,
    // so a pure config diff reports nothing separating it from `control` when the centring rule is
    // the whole experiment.
    const explicitRule = typeof block["center_rule"] === "string" ? block["center_rule"] : null;
    const centring = (explicitRule ?? name) === "gex" ? "GEX" : "ATM";
    return {
      name,
      enabled,
      retired: !enabled && row !== undefined,
      removed: false,
      notes: collectNotes(block),
      overrides: buildOverrides(block, defaults, shared),
      derived: [
        {
          label: "centres on",
          value: centring,
          detail: explicitRule === null ? "from the arm's own name — no center_rule set" : "set by center_rule",
        },
      ],
      firstSession: row?.first ?? null,
      lastSession: row?.last ?? null,
      positions: row?.n ?? 0,
    };
  });

  const advised = advisedEntries(seen, adviceDeclOf(doc, "base_arm"), "arm");
  return {
    ...base,
    configMissing: false,
    groupNotes: collectNotes(armsBlock),
    breaks: measurementBreaks(dbPath),
    entries: order([
      ...entries,
      ...advised,
      ...removedEntries(seen, new Set([...entries, ...advised].map((e) => e.name))),
    ]),
  };
}

// ---------------------------------------------------------------------------------------------
// MEIC — risk profiles
// ---------------------------------------------------------------------------------------------

export function readMeicProfileGuide(config: ConsoleConfig, mode: TradingMode): ExperimentGuide {
  const base: ExperimentGuide = {
    module: "meic",
    mode,
    unit: "risk profile",
    groupNotes: [],
    breaks: [],
    entries: [],
    configMissing: true,
  };
  const doc = readJson(config.paths.meicRiskConfig);
  if (doc === null) return base;

  const profiles = (doc["profiles"] ?? {}) as Record<string, unknown>;
  // Unlike flies, a profile states every parameter rather than overriding a `defaults` block, so
  // the base values come from the module's own config and the majority rule does most of the work.
  const moduleConfig = readJson(path.join(config.paths.cherrypick, "config", "meic.json")) ?? {};
  const dbPath = path.join(config.paths.meicDir, mode === "live" ? "meic_trades.db" : "paper_trades.db");
  const seen = ledgerSpans(dbPath, "ic_trades", "risk_profile");

  const blocks = Object.entries(profiles).filter(
    ([n, v]) => !n.startsWith("_") && typeof v === "object" && v !== null,
  ) as Array<[string, Record<string, unknown>]>;
  const shared = majorityValues(blocks);

  const entries: ExperimentGuideEntry[] = blocks.map(([name, block]) => {
    const row = seen.get(name);
    const enabled = block["enabled"] === true;
    return {
      name,
      enabled,
      retired: !enabled && row !== undefined,
      removed: false,
      notes: collectNotes(block),
      overrides: buildOverrides(block, moduleConfig, shared),
      derived: [],
      firstSession: row?.first ?? null,
      lastSession: row?.last ?? null,
      positions: row?.n ?? 0,
    };
  });

  // The notes describing the profile set live at the root here, not inside `profiles`.
  const groupNotes = [...collectNotes(doc), ...collectNotes(profiles)];

  const advised = advisedEntries(seen, adviceDeclOf(moduleConfig, "base_profile"), "profile");
  return {
    ...base,
    configMissing: false,
    groupNotes,
    breaks: measurementBreaks(dbPath),
    entries: order([
      ...entries,
      ...advised,
      ...removedEntries(seen, new Set([...entries, ...advised].map((e) => e.name))),
    ]),
  };
}
