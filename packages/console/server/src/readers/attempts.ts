import path from "node:path";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { memoOnStore, memoReadOnlyDb, num, str, hasColumn, armColumnOf, type DatabaseHandle } from "./db.js";
import { isVoidedSession } from "./voidedSession.js";

/**
 * The entry-attempts ledger — one row per evaluated entry opportunity per arm.
 *
 * This is the read side of the measurement both modules started keeping on
 * 2026-08-11, when each arm became an independent portfolio with unbounded
 * capital. Every arm now sees the same market with the same money, so the only
 * thing that differentiates them is which entries the rules let through: the
 * refusals are the primary signal, not a diagnostic.
 *
 * MEIC and flies keep separate tables with the same shape (`entry_attempts`
 * and `fly_entry_attempts`), deliberately — each lives in its own module's
 * ledger and neither module may read the other's — so this normalizes the two
 * into one row type for the UI rather than making the pages know the
 * difference.
 */

/** The outcome taxonomy both modules write. `noFill` is deliberately distinct
 *  from a gate refusal: under a fill-based cadence clock an entry that cleared
 *  every gate and simply did not fill neither spent the arm's slot nor was
 *  refused by a rule, and folding the two together makes the gates look
 *  stricter than they are. */
export type AttemptOutcome =
  | "filled"
  | "cadence_blocked"
  | "sign_rule_blocked"
  | "duplicate_blocked"
  | "gate_blocked"
  | "window_blocked"
  | "no_candidate"
  | "no_fill";

/** One timeline mark. Carries only what the timeline chart and its hover line read: the row's day
 *  is the payload's `tradeDate`, and the strike a module stands in for a centre was never drawn,
 *  so neither is sent (they were ~16% of a 1.6 MB flies payload polled every 20s). */
export interface AttemptRow {
  ts: string | null;
  arm: string;
  symbol: string | null;
  outcome: string;
  blockDetail: string | null;
  blockingStrike: number | null;
  secondsUntilCadenceClear: number | null;
  spot: number | null;
}

/** The page-level narrowing for one module's attempts: the arm (and symbol) the header selects.
 *  The column is `arm` in every ledger this reader serves (via `armColumnOf`) -- MEIC's page calls
 *  its own scope key `profile`, and the route maps it before it reaches here. Era is deliberately
 *  absent: no attempts table carries an era column, so an era narrows the resolved DAY (the route's
 *  job, through `resolveMeicSession`), never the rows. */
export interface AttemptsScope {
  arm: string | null;
  symbol: string | null;
}

export const NO_ATTEMPTS_SCOPE: AttemptsScope = { arm: null, symbol: null };

export interface ArmRailEntry {
  arm: string;
  attempts: number;
  fills: number;
  /** Sessions this arm has EVALUATED in, and sessions it has actually filled in — across the whole
   *  ledger, not just today. An arm that is enabled and running produces attempt rows even when it
   *  takes nothing, so a large gap between these two is the signature of an arm quietly doing
   *  nothing for days: the failure the rail exists to catch, and one a single day cannot show. */
  sessionsSeen: number;
  sessionsWithFills: number;
  /** MEIC only: resolved trades this arm holds today, and how many had BOTH short sides stopped.
   *  A single-side stop is the design working and averages about -$15; a double stop means price
   *  crossed both shorts in one session, paying to close both and collecting nothing, and averages
   *  -$149. It is the number that most directly says whether an arm's stop policy is working, and
   *  it was previously only reachable through the deep analytics. Null for flies, which has no
   *  two-sided structure. */
  resolvedToday: number | null;
  doubleStoppedToday: number | null;
  /** Refusals by outcome — the shape the rail's "why is this arm quiet" read needs. */
  refusals: Record<string, number>;
  /** The most recent refusal reason, or null if the last thing it did was fill. */
  lastRefusal: string | null;
  lastAttemptTs: string | null;
  lastFillTs: string | null;
}

/** A reason two sessions cannot be pooled — a cadence change, an arm added mid-session. Recorded
 *  by the modules themselves; surfaced here so a reader cannot pool across one unknowingly. */
export interface MeasurementBreak {
  arm: string;
  reason: string;
}

export interface AttemptsPayload {
  mode: TradingMode;
  module: AttemptsModule;
  tradeDate: string | null;
  /** Flies records these as mode='cadence' rows in its decision journal; MEIC keeps a dedicated
   *  `measurement_breaks` table. Different homes, one shape here — the pages should not have to
   *  know which module stores it where. */
  breaks: MeasurementBreak[];
  arms: ArmRailEntry[];
  /** Every attempt for the day, oldest first, THINNED for transport -- see downsampleTimeline.
   *  The attempt timeline chart's source; never the source for an exact count (that's
   *  blockedByStrike below), since thinning can drop repeated same-outcome rows. */
  timeline: AttemptRow[];
  /** Exact `sign_rule_blocked` counts per "arm|strike", computed from the FULL (untinned) rows --
   *  OccupancyMap's "refused N entries today" reads this instead of counting timeline rows itself,
   *  so thinning the timeline for the chart can never silently undercount it. */
  blockedByStrike: Record<string, number>;
}

/**
 * Give every attempt a timestamp the client can actually parse.
 *
 * The two modules stamp their rows differently and neither is wrong in its own
 * ledger: flies writes a full ET ISO string (`clock.now_iso()`), MEIC writes its
 * snapshot's `now_et`, which is a bare `HH:MM`. Left alone, `Date.parse` returns
 * NaN for the MEIC form — so every MEIC mark would silently vanish from the
 * timeline and the arm rail's clocks would all read "—", with the page looking
 * merely empty rather than broken. Normalizing here keeps that difference from
 * reaching the UI at all, rather than asking each view to remember it.
 *
 * The trade date supplies the missing day. Anything already carrying a date is
 * passed through untouched.
 */
function normalizeTs(ts: string | null, tradeDate: string | null): string | null {
  if (ts === null || ts === "") return null;
  if (ts.includes("T") || ts.includes(" ")) return ts;
  if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(ts) && tradeDate !== null) {
    return `${tradeDate}T${ts.length === 5 ? `${ts}:00` : ts}`;
  }
  return ts;
}

/**
 * Thin the timeline for transport, matching what the chart can actually show rather than what the
 * ledger recorded. AttemptTimeline draws a 1150px-wide SVG across a ~390-minute session -- about
 * 2.7px/minute -- so consecutive same-outcome ticks inside one minute (flies polls every 15s,
 * i.e. up to 4 identical marks a minute while an arm just sits refused) round to the same one or
 * two pixels and are visually indistinguishable already. Capping each (arm, outcome) to one row
 * per minute is a real reduction with no visible difference at this chart's own resolution.
 *
 * `filled` rows are NEVER dropped -- they're rare, they're the highest-value mark on the chart,
 * and unlike a refusal an entry's fill is the whole point of the day. Order is preserved (input is
 * already ts ASC), so the kept row per bucket is always the first one seen.
 */
function downsampleTimeline(rows: AttemptRow[]): AttemptRow[] {
  const seen = new Set<string>();
  const out: AttemptRow[] = [];
  for (const row of rows) {
    if (row.outcome === "filled") {
      out.push(row);
      continue;
    }
    const ms = row.ts !== null ? Date.parse(row.ts) : NaN;
    const bucket = Number.isNaN(ms) ? row.ts : Math.floor(ms / 60_000);
    const key = `${row.arm}|${row.outcome}|${bucket}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

/** Modules with a per-arm entry decision. See SPECS for why calendars is not one. */
import type { AttemptsModule } from "@console/shared";
export type { AttemptsModule };

interface TableSpec {
  file: (mode: TradingMode) => string;
  dir: (config: ConsoleConfig) => string;
  table: string;
  spotColumn: string;
  /** Columns only some ledgers keep. Absent ones read as null rather than failing the query. */
  blockingStrikeColumn?: string;
  cadenceColumn?: string;
}

/**
 * Which modules have a per-arm entry decision to show.
 *
 * **`calendars` is deliberately absent, and it is not an oversight.** It writes `dc_entry_attempts`
 * like the others, but that table has no arm column — by design: "one plan, N arms", where every
 * arm's positions for a week come from the SAME entry plan and the arms differ in exit policy
 * alone. There is no per-arm entry decision to rail, so its page shows the decision JOURNAL
 * instead, which is the right record for a module whose entry is unconditional.
 */
const SPECS: Record<AttemptsModule, TableSpec> = {
  meic: {
    file: (mode) => (mode === "live" ? "meic_trades.db" : "paper_trades.db"),
    dir: (config) => config.paths.meicDir,
    table: "entry_attempts",
    spotColumn: "underlying_price",
    blockingStrikeColumn: "blocking_strike",
    cadenceColumn: "seconds_until_cadence_clear",
  },
  flies: {
    file: (mode) => (mode === "live" ? "live_trades.db" : "paper_trades.db"),
    dir: (config) => config.paths.fliesDir,
    table: "fly_entry_attempts",
    spotColumn: "spot",
    blockingStrikeColumn: "blocking_strike",
    cadenceColumn: "seconds_until_cadence_clear",
  },
  pmcc: {
    // Paper-only and credential-free, so there is no live ledger to select.
    file: () => "paper_trades.db",
    dir: (config) => config.paths.pmccDir,
    table: "pmcc_entry_attempts",
    spotColumn: "spot",
    // pmcc keeps neither: it has no same-strike blocking rule and no entry cadence gate.
  },
  curve: {
    // Paper-only and credential-free, same as pmcc -- one ledger, no live/paper split to parse.
    file: () => "paper_trades.db",
    dir: (config) => config.paths.curveDir,
    table: "curve_entry_attempts",
    spotColumn: "spot",
    // curve keeps neither: no same-strike blocking rule, no entry cadence gate.
  },
};

/**
 * The arm rail and the attempt timeline for one module on one day.
 *
 * Returns an empty payload rather than throwing when the table is absent: a
 * ledger written by a checkout that predates the attempts work is a legitimate
 * state, and the console is read-only over every other package's data — it must
 * degrade to "nothing recorded" rather than fail the page.
 */
export function readEntryAttempts(
  config: ConsoleConfig,
  module: AttemptsModule,
  mode: TradingMode,
  day: string | null,
  scope: AttemptsScope = NO_ATTEMPTS_SCOPE,
): AttemptsPayload {
  const spec = SPECS[module];
  const dbPath = path.join(spec.dir(config), spec.file(mode));
  const empty: AttemptsPayload = {
    mode,
    module,
    tradeDate: null,
    breaks: [],
    arms: [],
    timeline: [],
    blockedByStrike: {},
  };

  // Memoised on the ledger's stamp. The flies ledger holds ~500k attempt rows and ~26k of them a
  // day, the page polls every 20s and the Overview every 15s, and better-sqlite3 is synchronous, so
  // every rebuild holds the event loop; between the module's writes (and all evening) the answer
  // cannot have moved. A read that threw is not remembered (memoReadOnlyDb).
  const key = `attempts:${dbPath}:${day ?? "latest"}:${scope.arm ?? "*"}:${scope.symbol ?? "*"}`;
  return memoReadOnlyDb<AttemptsPayload>(dbPath, key, empty, (db) => {
    if (!hasColumn(db, spec.table, "outcome")) return empty;
    // Resolved per FILE, not per module: meic's ledger says `risk_profile` until its own window,
    // and a live ledger can be on the other side of that window from its paper twin.
    const armColumn = armColumnOf(db, spec.table);

    // The page's arm/symbol narrowing, applied to EVERY query below that answers for "this day" --
    // the timeline, the rail's lifetime counts and MEIC's double-stop read -- so no two describe
    // different scopes. The header's dropdowns used to move the other MEIC slides while this page
    // drew every arm (2026-10-07, the forest's own bug on a second surface).
    const scopeClauses: string[] = [];
    const scopeParams: string[] = [];
    if (scope.arm !== null) {
      scopeClauses.push(`${armColumn} = ?`);
      scopeParams.push(scope.arm);
    }
    if (scope.symbol !== null) {
      scopeClauses.push("symbol = ?");
      scopeParams.push(scope.symbol);
    }
    const scopeAnd = scopeClauses.length > 0 ? ` AND ${scopeClauses.join(" AND ")}` : "";

    const dayRow = day
      ? { d: day }
      : db.prepare<[], { d: string }>(`SELECT MAX(trade_date) AS d FROM ${spec.table}`).get();
    const tradeDate = dayRow?.d ?? null;
    if (tradeDate === null) return empty;
    // A voided session (voidedSession.ts) lists nothing: every fill it made became no position, so
    // a timeline of them would read as trading that never counted.
    if (isVoidedSession(db, module, tradeDate)) return { ...empty, tradeDate };

    const rows = db
      .prepare<unknown[], Record<string, unknown>>(
        `SELECT ts, trade_date, ${armColumn} AS arm, symbol, outcome, block_detail,
                ${spec.blockingStrikeColumn ?? "NULL"} AS blocking_strike,
                ${spec.cadenceColumn ?? "NULL"} AS seconds_until_cadence_clear,
                ${spec.spotColumn} AS spot
           FROM ${spec.table}
          WHERE trade_date = ?${scopeAnd}
          ORDER BY ts ASC, id ASC`,
      )
      .all(tradeDate, ...scopeParams);

    const timeline: AttemptRow[] = rows.map((r) => ({
      ts: normalizeTs(str(r["ts"]), str(r["trade_date"])),
      arm: str(r["arm"]) ?? "?",
      symbol: str(r["symbol"]),
      outcome: str(r["outcome"]) ?? "gate_blocked",
      blockDetail: str(r["block_detail"]),
      blockingStrike: num(r["blocking_strike"]),
      secondsUntilCadenceClear: num(r["seconds_until_cadence_clear"]),
      spot: num(r["spot"]),
    }));

    const byArm = new Map<string, ArmRailEntry>();
    for (const row of timeline) {
      let entry = byArm.get(row.arm);
      if (entry === undefined) {
        entry = {
          arm: row.arm,
          attempts: 0,
          fills: 0,
          sessionsSeen: 0,
          sessionsWithFills: 0,
          resolvedToday: null,
          doubleStoppedToday: null,
          refusals: {},
          lastRefusal: null,
          lastAttemptTs: null,
          lastFillTs: null,
        };
        byArm.set(row.arm, entry);
      }
      entry.attempts += 1;
      entry.lastAttemptTs = row.ts;
      if (row.outcome === "filled") {
        entry.fills += 1;
        entry.lastFillTs = row.ts;
        // Cleared on a fill so the rail reads "what is holding this arm back
        // RIGHT NOW" rather than surfacing a refusal the arm has since moved past.
        entry.lastRefusal = null;
      } else {
        entry.refusals[row.outcome] = (entry.refusals[row.outcome] ?? 0) + 1;
        entry.lastRefusal = row.blockDetail ?? row.outcome;
      }
    }

    // Why sessions either side of this day cannot be pooled. Each module keeps these in its own
    // home — flies as mode='cadence' rows in its decision journal, MEIC in a dedicated
    // `measurement_breaks` table — and both degrade to none rather than failing the page, since a
    // ledger predating either lane is a legitimate state.
    let breaks: MeasurementBreak[] = [];
    try {
      // A break is a property of the DAY, so a module-wide row (flies: null arm; MEIC: '*') stays
      // visible under any arm scope; a row naming another arm does not.
      const armParam = scope.arm !== null ? [scope.arm] : [];
      breaks =
        module === "flies"
          ? db
              .prepare<unknown[], { arm: string | null; reason: string | null }>(
                `SELECT arm, reason FROM fly_decisions WHERE trade_date = ? AND mode = 'cadence'${
                  scope.arm !== null ? " AND (arm = ? OR arm IS NULL)" : ""
                }`,
              )
              .all(tradeDate, ...armParam)
              .map((r) => ({ arm: r.arm ?? "*", reason: r.reason ?? "" }))
          : db
              .prepare<unknown[], { arm: string | null; reason: string | null }>(
                `SELECT scope AS arm, reason FROM measurement_breaks WHERE break_date = ?${
                  scope.arm !== null ? " AND (scope = ? OR scope = '*' OR scope IS NULL)" : ""
                } ORDER BY id`,
              )
              .all(tradeDate, ...armParam)
              .map((r) => ({ arm: r.arm ?? "*", reason: r.reason ?? "" }));
      breaks = breaks.filter((b) => b.reason !== "");
    } catch {
      breaks = [];
    }

    // Lifetime session counts per arm, joined onto today's rail -- the only way the rail can
    // distinguish "quiet this morning" from "has not traded since it was added".
    try {
      const lifetime = readLifetimeSessions(db, spec.table, armColumn, scope);
      for (const row of lifetime) {
        const entry = byArm.get(row.arm);
        if (entry !== undefined) {
          entry.sessionsSeen = row.seen;
          entry.sessionsWithFills = row.filled;
        }
      }
    } catch {
      // A ledger too old to group is not a reason to fail the rail.
    }

    // Double stops, per arm, for the day in view. MEIC only: flies has no two-sided structure to
    // stop out twice. Read from the leg PAIR, the same definition analytics.break_even uses, so the
    // rail and the arm scorecard cannot disagree.
    if (module === "meic") {
      try {
        const icArm = armColumnOf(db, "ic_trades");
        const pairClauses = scope.arm !== null ? [` AND t.${icArm} = ?`] : [];
        const pairParams = scope.arm !== null ? [scope.arm] : [];
        if (scope.symbol !== null) {
          pairClauses.push(" AND t.symbol = ?");
          pairParams.push(scope.symbol);
        }
        const pairs = db
          .prepare<unknown[], { arm: string; resolved: number; doubles: number }>(
            `SELECT ${icArm} AS arm,
                    COUNT(*) AS resolved,
                    SUM(CASE WHEN put_status = 'stopped' AND call_status = 'stopped' THEN 1 ELSE 0 END) AS doubles
               FROM (SELECT t.ic_order_id, t.${icArm},
                            MAX(CASE WHEN l.side = 'put' THEN l.status END) AS put_status,
                            MAX(CASE WHEN l.side = 'call' THEN l.status END) AS call_status
                       FROM ic_trades t JOIN ic_spread_legs l ON l.ic_order_id = t.ic_order_id
                      WHERE t.trade_date = ?${pairClauses.join("")} GROUP BY t.ic_order_id)
              GROUP BY 1`,
          )
          .all(tradeDate, ...pairParams);
        for (const row of pairs) {
          const entry = byArm.get(row.arm);
          if (entry !== undefined) {
            entry.resolvedToday = row.resolved;
            entry.doubleStoppedToday = row.doubles;
          }
        }
      } catch {
        // An older ledger without ic_spread_legs is not a reason to fail the rail.
      }
    }

    // Computed from the FULL rows, before thinning -- OccupancyMap's "refused N entries today"
    // reads this instead of counting timeline rows itself.
    const blockedByStrike: Record<string, number> = {};
    for (const row of timeline) {
      if (row.outcome !== "sign_rule_blocked" || row.blockingStrike === null) continue;
      const key = `${row.arm}|${row.blockingStrike}`;
      blockedByStrike[key] = (blockedByStrike[key] ?? 0) + 1;
    }

    return {
      mode,
      module,
      tradeDate,
      breaks,
      arms: [...byArm.values()].sort((a, b) => a.arm.localeCompare(b.arm)),
      timeline: downsampleTimeline(timeline),
      blockedByStrike,
    };
  });
}

/**
 * Sessions each arm has evaluated in, and filled in, across the whole ledger (within the page's
 * arm/symbol scope -- a scoped symbol counts the days that produced attempts for that symbol).
 *
 * Shaped for the indexes rather than as one grouped scan: the distinct (day, arm) pairs come off
 * the `(trade_date, arm)` index alone, and "did this arm fill that day" is a seek per pair on an
 * index leading with `trade_date` and `outcome` -- or, where a ledger has one, `(arm, trade_date,
 * outcome)`, which answers it without touching the table. The single `COUNT(DISTINCT ...)` scan
 * this replaced read every row of a ~500k-row flies ledger (~480ms against ~65ms). Memoised on the
 * store's stamp, and keyed apart from the day, so browsing sessions does not recount it.
 */
function readLifetimeSessions(
  db: DatabaseHandle,
  table: string,
  armColumn: string,
  scope: AttemptsScope,
): Array<{ arm: string; seen: number; filled: number }> {
  const key = `attempts-lifetime:${db.name}:${table}:${armColumn}:${scope.arm ?? "*"}:${scope.symbol ?? "*"}`;
  return memoOnStore(db.name, key, () => {
    const pairClauses = ["trade_date IS NOT NULL"];
    const pairParams: string[] = [];
    if (scope.arm !== null) {
      pairClauses.push(`${armColumn} = ?`);
      pairParams.push(scope.arm);
    }
    if (scope.symbol !== null) {
      pairClauses.push("symbol = ?");
      pairParams.push(scope.symbol);
    }
    const fillSymbol = scope.symbol !== null ? " AND f.symbol = ?" : "";
    const fillParams = scope.symbol !== null ? [scope.symbol] : [];
    return db
      .prepare<unknown[], { arm: string; seen: number; filled: number }>(
        `SELECT arm, COUNT(*) AS seen, SUM(filled) AS filled
           FROM (SELECT d.arm AS arm,
                        EXISTS (SELECT 1 FROM ${table} f
                                 WHERE f.trade_date = d.trade_date AND f.outcome = 'filled'
                                   AND f.${armColumn} = d.arm${fillSymbol}) AS filled
                   FROM (SELECT DISTINCT trade_date, ${armColumn} AS arm FROM ${table}
                          WHERE ${pairClauses.join(" AND ")}) d)
          GROUP BY arm`,
      )
      .all(...fillParams, ...pairParams);
  });
}

/** One module's entry outcomes for a session, counted exactly. */
export interface AttemptCounts {
  tradeDate: string | null;
  filled: number;
  /** Every refusal: anything neither filled nor `no_fill`. */
  refused: number;
  noFill: number;
  /** The most frequent refusal reason (`block_detail`, else the outcome) with its count. */
  topRefusal: string | null;
}

const NO_COUNTS: AttemptCounts = { tradeDate: null, filled: 0, refused: 0, noFill: 0, topRefusal: null };

/**
 * The Overview's entries row for one module: filled / refused / no-fill for a session, from one
 * grouped query over that day's rows.
 *
 * The Overview used to count `readEntryAttempts(...).timeline`, which is THINNED for the chart (one
 * mark per arm, outcome and minute) -- so an arm refused on every 15s tick counted once a minute,
 * and "refused" read a fraction of the truth. It also built the whole page payload (every row of
 * the day, the arm rail, lifetime counts) to read four numbers from it.
 *
 * Same session rules as `readEntryAttempts`: `day` null means the table's latest day, and a voided
 * session counts nothing. Same refusal key as before (`block_detail`, else the outcome), with ties
 * broken by which reason appeared first.
 */
export function readAttemptCounts(
  config: ConsoleConfig,
  module: AttemptsModule,
  mode: TradingMode,
  day: string | null,
): AttemptCounts {
  const spec = SPECS[module];
  const dbPath = path.join(spec.dir(config), spec.file(mode));
  return memoReadOnlyDb<AttemptCounts>(dbPath, `attempt-counts:${dbPath}:${day ?? "latest"}`, NO_COUNTS, (db) => {
    if (!hasColumn(db, spec.table, "outcome")) return NO_COUNTS;
    const tradeDate =
      day ?? db.prepare<[], { d: string | null }>(`SELECT MAX(trade_date) AS d FROM ${spec.table}`).get()?.d ?? null;
    if (tradeDate === null) return NO_COUNTS;
    if (isVoidedSession(db, module, tradeDate)) return { ...NO_COUNTS, tradeDate };
    const groups = db
      .prepare<[string], { outcome: unknown; block_detail: unknown; n: number; first: number }>(
        `SELECT outcome, block_detail, COUNT(*) AS n, MIN(id) AS first
           FROM ${spec.table} WHERE trade_date = ? GROUP BY outcome, block_detail`,
      )
      .all(tradeDate);
    return countGroups(tradeDate, groups);
  });
}

/** Fold `(outcome, block_detail, n)` groups into the entries row. Exported for the test. */
export function countGroups(
  tradeDate: string | null,
  groups: ReadonlyArray<{ outcome: unknown; block_detail: unknown; n: number; first?: number }>,
): AttemptCounts {
  let filled = 0;
  let noFill = 0;
  const refusals = new Map<string, { n: number; first: number }>();
  for (const g of groups) {
    // The timeline's own reading of a row: a missing outcome is a gate refusal.
    const outcome = str(g.outcome) ?? "gate_blocked";
    if (outcome === "filled") filled += g.n;
    else if (outcome === "no_fill") noFill += g.n;
    else {
      const key = str(g.block_detail) ?? outcome;
      const prior = refusals.get(key);
      const first = g.first ?? Number.POSITIVE_INFINITY;
      refusals.set(key, { n: (prior?.n ?? 0) + g.n, first: Math.min(prior?.first ?? first, first) });
    }
  }
  const ranked = [...refusals.entries()].sort((a, b) => b[1].n - a[1].n || a[1].first - b[1].first);
  const refused = ranked.reduce((s, [, r]) => s + r.n, 0);
  const top = ranked[0];
  return {
    tradeDate,
    filled,
    refused,
    noFill,
    topRefusal: top !== undefined ? `${top[0]} ×${String(top[1].n)}` : null,
  };
}
