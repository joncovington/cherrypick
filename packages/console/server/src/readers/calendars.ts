import fs from "node:fs";
import path from "node:path";
import type {
  CalendarsBookCell,
  CalendarsEmRow,
  CalendarsEntryWindow,
  CalendarsIntegrity,
  CalendarsLeg,
  CalendarsPayload,
  CalendarsPosition,
  CalendarsWeekRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str, type DatabaseHandle } from "./db.js";
import { readCalendarsPlan } from "../services/calendarsBridge.js";

/**
 * The weekly double-calendar module's read layer.
 *
 * Paper only, and structurally so: there is no live loop, no live store, and no `mode` anywhere in
 * this file. The config's `live.enabled` is a documented inert placeholder
 * (packages/calendars/CLAUDE.md).
 *
 * The queries here mirror `packages/calendars/src/cherrypick/calendars/analytics.py`, that module's
 * stated one query layer, and each function below names the analytics function it answers for. Two
 * things it does NOT mirror — the exit-policy derivation and the week's calendar anchors — go out
 * through `services/calendarsBridge.ts` instead, for the reasons stated there.
 *
 * The rule the Python layer keeps and this one must keep too: `None` never means zero. An open week
 * reports a null net, not $0.00, because "not recorded" and "was zero" are different facts.
 *
 * What the module's own honesty rules oblige this file to surface, not merely permit: a refused
 * mark is a row and its refusal has a name; a measurement break is a row, not a memory; structure
 * tags never pool, so every grouping here carries the tag.
 */

const DB_FILE = "paper_trades.db";
const CADENCE_FILE = "tick_cadence.json";

/** Days before a declared dividend calendar lapses that the page starts asking for a refresh. */
const DIVIDEND_WARN_DAYS = 30;

/**
 * Columns this console build knows, per migrated table — the TypeScript half of the module's
 * `db.stale_writer_columns` guard, transcribed for the same reason `readers/pmcc.ts` transcribes
 * pmcc's: migration is additive and permanent, so a ledger opened once by a newer checkout keeps
 * columns an older reader will silently miss. Columns the ledger HAS that this file does not know
 * mean the writer has moved on and this page may be reading a narrower story than the module is
 * recording.
 *
 * REFRESH THIS when `db.py`'s `_SCHEMA`/`_ADDED_COLUMNS` gain a column. Being out of date is the
 * condition it reports, not a bug in it — it goes amber, it does not go wrong.
 */
const KNOWN_COLUMNS: Record<string, string[]> = {
  dc_positions: [
    "id", "position_id", "week_of", "entry_session", "book", "side", "symbol", "structure",
    "front_expiration", "back_expiration", "strike", "quantity", "entry_time", "entry_debit",
    "entry_cost", "entry_slippage", "entry_spot", "entry_em", "entry_em_pct",
    "entry_front_atm_call_mid", "entry_front_atm_put_mid", "entry_front_iv", "entry_back_iv",
    "entry_term_structure", "entry_context", "advice_params", "status", "exit_reason", "closed_at",
    "closed_session", "exit_value", "exit_cost", "exit_slippage", "settlement_spot",
    "itm_settlements", "gross_pnl", "fees", "created_at", "updated_at",
  ],
  dc_legs: [
    "id", "position_id", "leg_role", "occ_symbol", "streamer_symbol", "expiration", "strike",
    "option_type", "action", "quantity", "entry_bid", "entry_ask", "entry_mid", "entry_iv",
    "entry_delta", "status", "close_kind", "closed_at", "close_bid", "close_ask", "close_value",
    "created_at", "updated_at",
  ],
  dc_marks: [
    "id", "position_id", "leg_role", "marked_at", "session_date", "bid", "ask", "mid", "delta",
    "iv", "vega", "spot", "quote_age_s", "usable", "refusal",
  ],
  dc_assignments: [
    "id", "position_id", "leg_role", "symbol", "assigned_session", "assigned_at", "direction",
    "shares", "basis", "strike", "option_type", "status", "disposed_session", "disposed_at",
    "disposal_price", "share_pnl", "fees", "created_at", "updated_at",
  ],
};

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.calendarsDir, DB_FILE);
}

function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function obj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

interface CalendarsParams {
  symbols: string[];
  quantity: number | null;
  emFactor: number | null;
  entryWindowStart: string | null;
  entryWindowEnd: string | null;
  exitWindowStart: string | null;
  exitWindowEnd: string | null;
  maxQuoteAgeSeconds: number | null;
  maxLegSpreadPct: number | null;
  books: Array<{ name: string; enabled: boolean }>;
  adviceEnabled: boolean;
  dividends: Record<string, { declaredThrough: string | null; exDates: string[] }>;
  settlement: Record<string, string>;
}

/**
 * The module's declared knobs, resolved the way the module itself resolves them (`cli.load_config`:
 * the managed home, then the repo's config.json, then the shipped example). Missing config is not
 * fatal — the values degrade to null and the cards that need them say so rather than the page
 * failing whole.
 */
function loadParams(config: ConsoleConfig): CalendarsParams {
  let doc: Record<string, unknown> | null = null;
  for (const candidate of config.paths.calendarsConfigCandidates) {
    doc = readJson(candidate);
    if (doc !== null) break;
  }
  const defaults = obj(doc?.["defaults"]);
  const booksBlock = obj(doc?.["books"]);
  const dividendsBlock = obj(doc?.["dividends"]);

  const dividends: CalendarsParams["dividends"] = {};
  for (const [symbol, block] of Object.entries(dividendsBlock)) {
    if (symbol.startsWith("_")) continue;
    const b = obj(block);
    dividends[symbol] = {
      declaredThrough: str(b["declared_through"]),
      exDates: Array.isArray(b["ex_dates"]) ? b["ex_dates"].filter((d): d is string => typeof d === "string") : [],
    };
  }

  // `cash_settled_symbols` is the pre-SPY spelling and the module still reads it, so this must too
  // — a page reporting "undeclared" for a symbol the module settles fine would be worse than none.
  const settlement: Record<string, string> = {};
  for (const [symbol, style] of Object.entries(obj(doc?.["settlement_style"]))) {
    if (!symbol.startsWith("_") && typeof style === "string") settlement[symbol] = style;
  }
  const legacyCash = doc?.["cash_settled_symbols"];
  if (Array.isArray(legacyCash)) {
    for (const symbol of legacyCash) {
      if (typeof symbol === "string" && !(symbol in settlement)) settlement[symbol] = "cash";
    }
  }

  return {
    symbols: Array.isArray(doc?.["symbols"]) ? doc["symbols"].filter((s): s is string => typeof s === "string") : [],
    quantity: num(defaults["quantity"]),
    emFactor: num(defaults["em_factor"]),
    entryWindowStart: str(defaults["entry_window_start"]),
    entryWindowEnd: str(defaults["entry_window_end"]),
    exitWindowStart: str(defaults["exit_window_start"]),
    exitWindowEnd: str(defaults["exit_window_end"]),
    maxQuoteAgeSeconds: num(defaults["max_quote_age_seconds"]),
    maxLegSpreadPct: num(defaults["max_leg_spread_pct"]),
    books: Object.entries(booksBlock)
      .filter(([name]) => !name.startsWith("_"))
      .map(([name, block]) => ({ name, enabled: obj(block)["enabled"] === true })),
    adviceEnabled: obj(doc?.["advice"])["enabled"] === true,
    dividends,
    settlement,
  };
}

/**
 * The session every card on the page names.
 *
 * Resolved ONCE and unscoped, the flies lesson: a per-book "latest" lets one card answer for Monday
 * while the card beside it answers for Friday, both correctly labelled and irreconcilable. The
 * loop's own iterations are the primary source because this loop ticks all week on a strategy that
 * enters once — falling back to entries would name the last day something HAPPENED rather than the
 * last day the module RAN, and for a module whose first week took no position those differ exactly
 * when the page matters most.
 */
function latestSession(db: DatabaseHandle): string | null {
  const fromLoop = db
    .prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM dc_loop_iterations")
    .get()?.d;
  if (fromLoop != null) return fromLoop;
  return db.prepare<[], { d: string | null }>("SELECT MAX(entry_session) AS d FROM dc_positions").get()?.d ?? null;
}

function legsFor(db: DatabaseHandle, ids: string[]): Map<string, CalendarsLeg[]> {
  const out = new Map<string, CalendarsLeg[]>();
  if (ids.length === 0) return out;
  const marks = ids.map(() => "?").join(", ");
  for (const r of db
    .prepare<string[], Record<string, unknown>>(
      `SELECT position_id, leg_role, occ_symbol, expiration, strike, option_type, action, entry_mid,
              status, close_kind, close_value
         FROM dc_legs WHERE position_id IN (${marks}) ORDER BY position_id, leg_role`,
    )
    .all(...ids)) {
    const id = str(r["position_id"]) ?? "";
    const list = out.get(id) ?? [];
    list.push({
      legRole: str(r["leg_role"]) ?? "",
      occSymbol: str(r["occ_symbol"]) ?? "",
      expiration: str(r["expiration"]) ?? "",
      strike: num(r["strike"]),
      optionType: str(r["option_type"]) ?? "",
      action: str(r["action"]) ?? "",
      entryMid: num(r["entry_mid"]),
      status: str(r["status"]) ?? "",
      closeKind: str(r["close_kind"]),
      closeValue: num(r["close_value"]),
    });
    out.set(id, list);
  }
  return out;
}

function toPosition(r: Record<string, unknown>, legs: CalendarsLeg[]): CalendarsPosition {
  const gross = num(r["gross_pnl"]);
  const fees = num(r["fees"]);
  return {
    positionId: str(r["position_id"]) ?? "",
    weekOf: str(r["week_of"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    book: str(r["book"]) ?? "",
    side: str(r["side"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    structure: str(r["structure"]) ?? "",
    frontExpiration: str(r["front_expiration"]) ?? "",
    backExpiration: str(r["back_expiration"]) ?? "",
    strike: num(r["strike"]),
    quantity: num(r["quantity"]),
    entryDebit: num(r["entry_debit"]),
    entrySpot: num(r["entry_spot"]),
    entryEm: num(r["entry_em"]),
    entryEmPct: num(r["entry_em_pct"]),
    entryFrontIv: num(r["entry_front_iv"]),
    entryBackIv: num(r["entry_back_iv"]),
    entryTermStructure: num(r["entry_term_structure"]),
    status: str(r["status"]) ?? "",
    exitReason: str(r["exit_reason"]),
    closedSession: str(r["closed_session"]),
    settlementSpot: num(r["settlement_spot"]),
    itmSettlements: num(r["itm_settlements"]),
    grossPnl: gross,
    fees,
    // Null propagates: an unrecorded gross or fee is not a zero-cost week.
    netPnl: gross === null || fees === null ? null : Math.round((gross - fees) * 100) / 100,
    legs,
  };
}

function readPositions(db: DatabaseHandle, where: string, params: string[]): CalendarsPosition[] {
  const rows = db
    .prepare<string[], Record<string, unknown>>(
      `SELECT * FROM dc_positions WHERE ${where} ORDER BY week_of DESC, book, side`,
    )
    .all(...params);
  const legs = legsFor(db, rows.map((r) => str(r["position_id"]) ?? ""));
  return rows.map((r) => toPosition(r, legs.get(str(r["position_id"]) ?? "") ?? []));
}

/**
 * Mirrors `analytics.headline()`: per-book, per-structure results over CLOSED positions.
 *
 * Grouped by structure and never pooled across tags — the module's fourth honesty rule. Net is
 * `SUM(gross) - SUM(fees)`, the same single subtraction `cherrypick.core.ledgers` performs for the
 * `dc_week` schema.
 */
function readBooks(db: DatabaseHandle): CalendarsBookCell[] {
  return db
    .prepare<[], Record<string, unknown>>(
      `SELECT book, structure, COUNT(*) AS n, COUNT(DISTINCT week_of) AS weeks,
              SUM(gross_pnl) AS gross, SUM(fees) AS fees, SUM(gross_pnl) - SUM(fees) AS net,
              SUM((gross_pnl - fees) > 0) AS wins
         FROM dc_positions WHERE status = 'closed'
        GROUP BY book, structure ORDER BY book, structure`,
    )
    .all()
    .map((r) => {
      const n = Number(r["n"] ?? 0);
      const wins = num(r["wins"]);
      return {
        book: str(r["book"]) ?? "",
        structure: str(r["structure"]) ?? "",
        positions: n,
        weeks: Number(r["weeks"] ?? 0),
        grossPnl: num(r["gross"]),
        fees: num(r["fees"]),
        netPnl: num(r["net"]),
        winRate: n > 0 && wins !== null ? wins / n : null,
      };
    });
}

/** Mirrors `analytics.em_vs_realized()` — the strategy's premise, measured, one row per week. */
function readEmVsRealized(db: DatabaseHandle): CalendarsEmRow[] {
  return db
    .prepare<[], Record<string, unknown>>(
      `SELECT week_of, MIN(structure) AS structure, MIN(entry_spot) AS entry_spot, MIN(entry_em) AS em,
              MIN(settlement_spot) AS settle_spot
         FROM dc_positions WHERE book = 'path' AND settlement_spot IS NOT NULL
        GROUP BY week_of ORDER BY week_of DESC`,
    )
    .all()
    .map((r) => {
      const entrySpot = num(r["entry_spot"]);
      const settle = num(r["settle_spot"]);
      const em = num(r["em"]);
      const realized = entrySpot === null || settle === null ? null : Math.abs(settle - entrySpot);
      return {
        weekOf: str(r["week_of"]) ?? "",
        structure: str(r["structure"]) ?? "",
        expectedMove: em,
        realizedMove: realized,
        ratio: realized === null || em === null || em === 0 ? null : realized / em,
      };
    });
}

/** Mirrors `analytics.mark_coverage()` for one session. */
function readMarkCoverage(db: DatabaseHandle, session: string | null): CalendarsIntegrity["markCoverage"] {
  if (session === null) return { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] };
  const totals = db
    .prepare<[string], Record<string, unknown>>(
      "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM dc_marks WHERE session_date = ?",
    )
    .get(session);
  const marks = Number(totals?.["total"] ?? 0);
  const refused = Number(totals?.["refused"] ?? 0);
  const refusals = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT refusal, COUNT(*) AS n FROM dc_marks
        WHERE session_date = ? AND usable = 0 AND refusal IS NOT NULL
        GROUP BY refusal ORDER BY n DESC`,
    )
    .all(session)
    .map((r) => ({ reason: str(r["refusal"]) ?? "", n: Number(r["n"] ?? 0) }));
  return { session, marks, refused, refusalShare: marks > 0 ? refused / marks : null, refusals };
}

/** Columns the ledger has that this build does not know — see KNOWN_COLUMNS. */
function schemaDrift(db: DatabaseHandle): string[] {
  const drift: string[] = [];
  for (const [table, known] of Object.entries(KNOWN_COLUMNS)) {
    const knownSet = new Set(known);
    let present: Array<Record<string, unknown>>;
    try {
      present = db.prepare<[], Record<string, unknown>>(`PRAGMA table_info(${table})`).all();
    } catch {
      continue;
    }
    for (const col of present) {
      const name = str(col["name"]);
      if (name !== null && !knownSet.has(name)) drift.push(`${table}.${name}`);
    }
  }
  return drift.sort();
}

/** Whether a declared ex-dividend calendar has lapsed, or lapses inside the warning window. */
function dividendRefreshDue(declaredThrough: string | null, session: string | null): boolean {
  // No declared date at all is the loudest case: the module refuses the week outright.
  if (declaredThrough === null) return true;
  if (session === null) return false;
  const through = Date.parse(`${declaredThrough}T00:00:00Z`);
  const on = Date.parse(`${session}T00:00:00Z`);
  if (Number.isNaN(through) || Number.isNaN(on)) return false;
  return through - on < DIVIDEND_WARN_DAYS * 86_400_000;
}

/**
 * What the entry day did with its one window.
 *
 * Read against the PLAN's entry session where the bridge could give one, so a Wednesday reader sees
 * Monday's decision rather than an empty Wednesday. Entry here is unconditional by design, so an
 * empty week is never "no setup was there" — it is a refusal with a name, and that name plus the
 * feed counts underneath it is the whole card.
 */
function readEntryWindow(
  db: DatabaseHandle,
  params: CalendarsParams,
  session: string | null,
): CalendarsEntryWindow {
  const empty: CalendarsEntryWindow = {
    session,
    windowStart: params.entryWindowStart,
    windowEnd: params.entryWindowEnd,
    attempts: [],
    entered: false,
    skipReason: null,
    skipOccurrences: 0,
    feed: null,
  };
  if (session === null) return empty;

  const attempts = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT outcome, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts,
              MAX(spot) AS spot, MAX(em) AS em, MAX(put_strike) AS put_strike,
              MAX(call_strike) AS call_strike, MAX(put_debit) AS put_debit, MAX(call_debit) AS call_debit
         FROM dc_entry_attempts WHERE trade_date = ?
        GROUP BY outcome ORDER BY n DESC`,
    )
    .all(session)
    .map((r) => ({
      outcome: str(r["outcome"]) ?? "",
      n: Number(r["n"] ?? 0),
      firstTs: str(r["first_ts"]),
      lastTs: str(r["last_ts"]),
      spot: num(r["spot"]),
      em: num(r["em"]),
      putStrike: num(r["put_strike"]),
      callStrike: num(r["call_strike"]),
      putDebit: num(r["put_debit"]),
      callDebit: num(r["call_debit"]),
    }));

  const entered =
    Number(
      db
        .prepare<[string], { n: number }>("SELECT COUNT(*) AS n FROM dc_positions WHERE entry_session = ?")
        .get(session)?.n ?? 0,
    ) > 0;

  // The collapsed journal's word for the week going untraded. Only the not-accepted rows: an
  // accepted decision is the entry itself, which `entered` already says.
  const skip = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT reason, SUM(occurrences) AS n FROM dc_decisions
        WHERE trade_date = ? AND accepted = 0 AND mode = 'entry'
        GROUP BY reason ORDER BY n DESC LIMIT 1`,
    )
    .get(session);

  const feedRow = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT COUNT(*) AS ticks, SUM(COALESCE(quotes_fresh, 0)) AS fresh,
              SUM(COALESCE(quotes_stale, 0)) AS stale, SUM(spot IS NOT NULL) AS spot_ticks
         FROM dc_snapshots WHERE trade_date = ? AND kind = 'entry'`,
    )
    .get(session);
  const ticks = Number(feedRow?.["ticks"] ?? 0);

  return {
    ...empty,
    attempts,
    entered,
    skipReason: skip === undefined ? null : str(skip["reason"]),
    skipOccurrences: skip === undefined ? 0 : Number(skip["n"] ?? 0),
    feed:
      ticks === 0
        ? null
        : {
            ticks,
            fresh: Number(feedRow?.["fresh"] ?? 0),
            stale: Number(feedRow?.["stale"] ?? 0),
            spotTicks: Number(feedRow?.["spot_ticks"] ?? 0),
          },
  };
}

export function readCalendars(config: ConsoleConfig): CalendarsPayload {
  const params = loadParams(config);
  const { plan, error: planError } = readCalendarsPlan();
  const cadenceDoc = readJson(path.join(config.paths.calendarsDir, CADENCE_FILE));

  const paramsOut: CalendarsPayload["params"] = {
    symbols: params.symbols,
    quantity: params.quantity,
    emFactor: params.emFactor,
    entryWindowStart: params.entryWindowStart,
    entryWindowEnd: params.entryWindowEnd,
    exitWindowStart: params.exitWindowStart,
    exitWindowEnd: params.exitWindowEnd,
    maxQuoteAgeSeconds: params.maxQuoteAgeSeconds,
    maxLegSpreadPct: params.maxLegSpreadPct,
    books: params.books,
    adviceEnabled: params.adviceEnabled,
  };
  const dividends = params.symbols.map((symbol) => {
    const declared = params.dividends[symbol];
    return {
      symbol,
      declaredThrough: declared?.declaredThrough ?? null,
      exDates: declared?.exDates ?? [],
      refreshDue: dividendRefreshDue(declared?.declaredThrough ?? null, plan?.entrySession ?? null),
    };
  });
  const settlement = params.symbols.map((symbol) => ({ symbol, style: params.settlement[symbol] ?? null }));
  const tickCadence =
    cadenceDoc === null ? null : { seconds: num(cadenceDoc["seconds"]), since: str(cadenceDoc["since"]) };

  const empty: CalendarsPayload = {
    session: null,
    dbPresent: false,
    plan,
    planError,
    currentWeek: { weekOf: plan?.weekOf ?? null, positions: [] },
    entryWindow: {
      session: null,
      windowStart: params.entryWindowStart,
      windowEnd: params.entryWindowEnd,
      attempts: [],
      entered: false,
      skipReason: null,
      skipOccurrences: 0,
      feed: null,
    },
    openPositions: [],
    books: [],
    emVsRealized: [],
    integrity: {
      markCoverage: { session: null, marks: 0, refused: 0, refusalShare: null, refusals: [] },
      schemaDrift: [],
      measurementBreaks: [],
      tickCadence,
      dividends,
      settlement,
      openShareAssignments: 0,
    },
    today: { lastIteration: null, decisions: [] },
    params: paramsOut,
  };

  return withReadOnlyDb<CalendarsPayload>(dbPath(config), empty, (db) => {
    const session = latestSession(db);
    // The plan's entry session is the week the page speaks for. Where the bridge could not run,
    // fall back to the ledger's own latest entry session rather than showing no window at all.
    const entrySession =
      plan?.entrySession ??
      db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM dc_entry_attempts").get()?.d ??
      null;

    const weekOf = plan?.weekOf ?? db.prepare<[], { w: string | null }>("SELECT MAX(week_of) AS w FROM dc_positions").get()?.w ?? null;

    const iteration = db
      .prepare<[], Record<string, unknown>>(
        "SELECT ran_at, phase, status FROM dc_loop_iterations ORDER BY ran_at DESC LIMIT 1",
      )
      .get();
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);

    const decisions =
      session === null
        ? []
        : db
            .prepare<[string], Record<string, unknown>>(
              `SELECT book, reason, accepted, SUM(occurrences) AS n, MAX(last_ts) AS last_ts
                 FROM dc_decisions WHERE trade_date = ?
                GROUP BY book, reason, accepted ORDER BY n DESC`,
            )
            .all(session)
            .map((r) => ({
              book: str(r["book"]) ?? "",
              reason: str(r["reason"]) ?? "",
              accepted: r["accepted"] === 1,
              occurrences: Number(r["n"] ?? 0),
              lastTs: str(r["last_ts"]),
            }));

    const openShares = Number(
      db
        .prepare<[], { n: number }>("SELECT COUNT(*) AS n FROM dc_assignments WHERE status = 'open'")
        .get()?.n ?? 0,
    );

    const breaks = db
      .prepare<[], Record<string, unknown>>(
        "SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC",
      )
      .all()
      .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "", note: str(r["note"]) }));

    return {
      ...empty,
      session,
      dbPresent: true,
      currentWeek: {
        weekOf,
        positions: weekOf === null ? [] : readPositions(db, "week_of = ?", [weekOf]),
      },
      entryWindow: readEntryWindow(db, params, entrySession),
      openPositions: readPositions(db, "status != 'closed'", []),
      books: readBooks(db),
      emVsRealized: readEmVsRealized(db),
      integrity: {
        markCoverage: readMarkCoverage(db, session),
        schemaDrift: schemaDrift(db),
        measurementBreaks: breaks,
        tickCadence,
        dividends,
        settlement,
        openShareAssignments: openShares,
      },
      today: {
        lastIteration:
          iteration === undefined || ranAt === null
            ? null
            : {
                ranAt,
                phase: str(iteration["phase"]) ?? "",
                status: str(iteration["status"]) ?? "",
                ageSeconds: Math.max(0, Date.now() / 1000 - ranAt),
              },
        decisions,
      },
    };
  });
}

/**
 * One row per (week, book) — the history tab's index.
 *
 * Every book's positions for a week come from the SAME entry plan, so these rows are exactly paired
 * by construction and a divergence between two books on one week is exit policy and nothing else.
 * `closed` rides beside `positions` because a week does not finish while its delivered shares are
 * outstanding, and a partial week must not read as a finished one with a small net.
 */
export function readCalendarsWeeks(config: ConsoleConfig): CalendarsWeekRow[] {
  return withReadOnlyDb<CalendarsWeekRow[]>(dbPath(config), [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT week_of, book, MIN(structure) AS structure, MIN(entry_session) AS entry_session,
                COUNT(*) AS n, SUM(status = 'closed') AS closed, SUM(entry_debit) AS entry_debit,
                MIN(entry_spot) AS entry_spot, MAX(settlement_spot) AS settlement_spot,
                SUM(gross_pnl) AS gross, SUM(fees) AS fees
           FROM dc_positions GROUP BY week_of, book ORDER BY week_of DESC, book`,
      )
      .all()
      .map((r) => {
        const gross = num(r["gross"]);
        const fees = num(r["fees"]);
        const n = Number(r["n"] ?? 0);
        const closed = Number(r["closed"] ?? 0);
        return {
          weekOf: str(r["week_of"]) ?? "",
          structure: str(r["structure"]) ?? "",
          entrySession: str(r["entry_session"]) ?? "",
          book: str(r["book"]) ?? "",
          positions: n,
          closed,
          entryDebit: num(r["entry_debit"]),
          entrySpot: num(r["entry_spot"]),
          settlementSpot: num(r["settlement_spot"]),
          grossPnl: gross,
          fees,
          // An unfinished week has no net. Reporting the closed half's number under the week's name
          // would read as the week's result.
          netPnl: closed < n || gross === null || fees === null ? null : Math.round((gross - fees) * 100) / 100,
        };
      }),
  );
}

/** Mirrors `analytics.week_detail()` — everything on file for one week, legs included. */
export function readCalendarsWeek(config: ConsoleConfig, weekOf: string): CalendarsPosition[] {
  return withReadOnlyDb<CalendarsPosition[]>(dbPath(config), [], (db) =>
    readPositions(db, "week_of = ?", [weekOf]),
  );
}
