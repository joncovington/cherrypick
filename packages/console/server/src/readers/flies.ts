import path from "node:path";
import type { FliesPayload, FliesBookRow, FliesPositionRow, Paged, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { memoOnStore, withReadOnlyDb, num, str } from "./db.js";
import { emptyPage, pagedQuery, pageArray, FIRST_PAGE, type PageRequest } from "./paging.js";

/**
 * The era this module counts as evidence — the SPX 5-wide books from 2026-08-01.
 *
 * Flies has no `era` column, so an era here is a (symbol, start-date) pair rather than a tag. The
 * XSP books (2026-07-29..07-31) are a different trade, not an earlier version of this one: 1-wide
 * structures on a $750 index, where the median completed fly collected $12.00 against $4.97 of fees
 * — 41.4% drag against the SPX book's 10.9%. Credits, widths and per-contract risk all differ by
 * roughly 5x, so pooling them silently distorts every per-arm breakdown.
 *
 * Mirrors MEIC's `CURRENT_ERA`: narrow by default, widen only as a stated choice. Deliberately a
 * FILTER and never a deletion — the XSP books are the record of a documented fee finding, and the
 * module keeps negative results on purpose.
 */
export const CURRENT_ERA = { symbol: "SPX", from: "2026-08-01" } as const;

/**
 * Every era the ledger holds, declared rather than derived.
 *
 * There is no `era` column to group by, and the boundaries are facts about what the module was
 * trading rather than anything the rows announce — so they are written down here, matching the
 * module's own record (XSP 2026-07-29..07-31, SPX through 07-28, SPX 5-wide from 08-01).
 *
 * The point of listing them is that each is readable ALONE. Before this the control offered the
 * current era or "all", so the two earlier books could only be seen pooled with the current one —
 * which is the exact comparison the module says distorts every per-arm breakdown, and the only way
 * it offered to look at them at all.
 */
export const ERAS = [
  { key: "spx", label: "SPX 5-wide (current)", symbol: "SPX", from: "2026-08-01", to: null },
  { key: "xsp", label: "XSP 1-wide", symbol: "XSP", from: "2026-07-29", to: "2026-07-31" },
  { key: "spx-early", label: "SPX (pre-XSP)", symbol: "SPX", from: null, to: "2026-07-28" },
] as const;

export type FliesEraKey = (typeof ERAS)[number]["key"];

export interface FliesMeta {
  arms: string[];
  dates: string[];
  symbols: string[];
  /** Every declared era with how many positions it holds in THIS store. */
  eras: Array<{ era: string; label: string; trades: number }>;
  currentEra: string;
}

/** The era a null filter means: the module's own evidence window. */
export const DEFAULT_ERA: FliesEraKey = "spx";

/**
 * SQL for one era, as clause + params. "ALL" pools every era and is only ever a stated choice.
 *
 * One helper because three call sites applied this by hand and would otherwise drift — the same
 * shape of copy that let a stale expiration selector survive in four places in this suite.
 */
export function eraClause(era: string | null): { sql: string | null; params: string[] } {
  if (era === "ALL") return { sql: null, params: [] };
  const found = ERAS.find((e) => e.key === era) ?? ERAS.find((e) => e.key === DEFAULT_ERA)!;
  const parts = ["symbol = ?"];
  const params: string[] = [found.symbol];
  if (found.from !== null) {
    parts.push("trade_date >= ?");
    params.push(found.from);
  }
  if (found.to !== null) {
    parts.push("trade_date <= ?");
    params.push(found.to);
  }
  return { sql: parts.join(" AND "), params };
}

export interface FliesFilter {
  arm: string | null;
  date: string | null;
  /** null = every symbol in scope. Only meaningful with era "ALL"; the current era is SPX alone. */
  symbol: string | null;
  /** null = the current era; "ALL" = every era, deliberately. */
  era: string | null;
}

/** Books and positions page independently — they are two tables, not one list. */
export interface FliesPageRequest {
  books: PageRequest;
  positions: PageRequest;
}

export const FLIES_FIRST_PAGE: FliesPageRequest = { books: FIRST_PAGE, positions: FIRST_PAGE };

function filterSql(filter: FliesFilter): { where: string; params: string[] } {
  const clauses: string[] = [];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  if (filter.date !== null) {
    clauses.push("trade_date = ?");
    params.push(filter.date);
  }
  if (filter.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }
  // Era last, so an explicit date the caller asked for is never silently overridden — asking for a
  // specific XSP-era day and getting an empty page would read as "nothing happened" rather than
  // "filtered out", which is the failure the scope control exists to prevent.
  const era = eraClause(filter.era);
  if (era.sql !== null) {
    clauses.push(era.sql);
    params.push(...era.params);
  }
  return { where: clauses.length > 0 ? clauses.join(" AND ") : "1=1", params };
}

/**
 * The most recent session in the book. Deliberately unscoped by arm or era: every card on the Today
 * tab has to name the SAME day, and a per-arm "latest" would let the books table show one session
 * while the arm rail beside it showed another.
 */
function latestTradeDate(dbPath: string): string | null {
  return withReadOnlyDb<string | null>(
    dbPath,
    null,
    (db) => db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions").get()?.d ?? null,
  );
}

export function readFlies(
  config: ConsoleConfig,
  mode: TradingMode,
  filter: FliesFilter,
  page: FliesPageRequest = FLIES_FIRST_PAGE,
): FliesPayload {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  // "latest day" (a null date) is a DAY, not the absence of one. Left unresolved it reached the SQL
  // as no date clause at all, so the Today tab's books and positions quietly answered for every
  // session in the era while the cards above them answered for one — 289 rows beside a 34-position
  // day, both correctly labelled and irreconcilable. Resolve it here, exactly the way the analytics
  // and the arm rail already do (an unscoped MAX, so every card on the tab names the same day).
  const scoped: FliesFilter = { ...filter, date: filter.date ?? latestTradeDate(dbPath) };
  const { where, params } = filterSql(scoped);

  const books = withReadOnlyDb<Paged<FliesBookRow>>(dbPath, emptyPage(page.books), (db) =>
    pagedQuery<FliesBookRow>(
      db,
      {
        columns: `book_id, trade_date, arm, symbol, credit_collected, debits_paid, fees,
                  net_cash, floor_holds, band_low, band_high, pnl, status`,
        from: "fly_books",
        where,
        params,
        orderBy: "id DESC",
      },
      page.books,
      (r: Record<string, unknown>) => ({
        mode,
        bookId: str(r["book_id"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        arm: str(r["arm"]),
        symbol: str(r["symbol"]) ?? "",
        creditCollected: num(r["credit_collected"]),
        debitsPaid: num(r["debits_paid"]),
        fees: num(r["fees"]),
        netCash: num(r["net_cash"]),
        floorHolds: r["floor_holds"] === null ? null : r["floor_holds"] === 1,
        bandLow: num(r["band_low"]),
        bandHigh: num(r["band_high"]),
        pnl: num(r["pnl"]),
        status: str(r["status"]) ?? "",
      })),
  );

  const positions = withReadOnlyDb<Paged<FliesPositionRow>>(dbPath, emptyPage(page.positions), (db) =>
    pagedQuery<FliesPositionRow>(
      db,
      {
        columns: `position_id, trade_date, symbol, arm, entry_mode, kind, side, center, wing_width,
                  quantity, net, floor_dollars, risk_free, status, pnl, entry_time`,
        from: "fly_positions",
        where,
        params,
        orderBy: "id DESC",
      },
      page.positions,
      (r: Record<string, unknown>) => ({
        mode,
        positionId: str(r["position_id"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        arm: str(r["arm"]),
        entryMode: str(r["entry_mode"]),
        kind: str(r["kind"]),
        side: str(r["side"]),
        center: num(r["center"]),
        wingWidth: num(r["wing_width"]),
        quantity: num(r["quantity"]),
        net: num(r["net"]),
        floorDollars: num(r["floor_dollars"]),
        riskFree: r["risk_free"] === 1,
        status: str(r["status"]) ?? "",
        pnl: num(r["pnl"]),
        entryTime: str(r["entry_time"]),
      })),
  );

  return { mode, books, positions };
}

import { payoffCurve, stateAt, bookPnl, positionFloor, type FlyPosition, type FlyRow, type PayoffCurve } from "../analytics/fliesPayoff.js";

export interface FliesForest {
  mode: TradingMode;
  tradeDate: string | null;
  /** The day's traded underlying (most-traded symbol when mixed) — the
   *  client subscribes this for the live spot line, never a hardcoded one. */
  symbol: string | null;
  /** One curve per arm active on the day. */
  arms: Array<{ arm: string; curve: PayoffCurve }>;
  /** The day's settlement print when the session has settled; null intraday. */
  settlement: { price: number; source: string | null } | null;
  /** Last recorded intraday tick for the day, for the settled-vs-close note. */
  lastTickSpot: number | null;
}

/**
 * Distinct arms and trade dates, for the page's filter selects — narrowed to the same era as the
 * data itself.
 *
 * The selects have to agree with the scope or they lie about what is reachable: `width-2`..`width-4`
 * traded XSP-era under a different meaning of the name (a raw point wing_width, disabled on the SPX
 * move because those point values aren't buildable on SPX's 5-point strikes) and only resumed
 * 2026-08-15 on SPX under a strike-count sweep (`wing_width_strikes`) — so an unfiltered list mixes
 * two eras' worth of geometry under the same arm tag and dates that return an empty page for the
 * narrower one. An option that yields no rows reads as "nothing happened" rather than "not in this
 * era", which is exactly the confusion the scope control exists to remove.
 */
export function readFliesMeta(
  config: ConsoleConfig,
  mode: TradingMode,
  era: string | null = null,
): FliesMeta {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const ec = eraClause(era);
  const scope = ec.sql === null ? "" : ` AND ${ec.sql}`;
  const params: string[] = ec.params;
  return withReadOnlyDb<FliesMeta>(
    dbPath,
    { arms: [], dates: [], symbols: [], eras: [], currentEra: DEFAULT_ERA },
    (db) => ({
      // Every era with what it holds, so choosing one is a choice between known quantities rather
      // than a guess — and an era this store never traded reads as 0 rather than vanishing.
      eras: ERAS.map((e) => {
        const c = eraClause(e.key);
        const row = db
          .prepare<string[], { n: number }>(
            `SELECT COUNT(*) AS n FROM fly_positions WHERE ${c.sql ?? "1=1"}`,
          )
          .get(...c.params);
        return { era: e.key, label: e.label, trades: Number(row?.n ?? 0) };
      }),
      currentEra: DEFAULT_ERA,
      arms: db
        .prepare<string[], { arm: string }>(
          `SELECT DISTINCT arm FROM fly_positions WHERE arm IS NOT NULL${scope} ORDER BY arm`,
        )
        .all(...params)
        .map((r) => r.arm),
      // Sessions the LOOP ran, not sessions that produced positions. `fly_iterations` gets a row
      // every tick whether or not anything filled, so a morning that has been evaluating for an
      // hour without an entry is still a session — and it is the day the attempts views are
      // showing. Listing only days with positions meant the page's own "latest day" could resolve
      // to yesterday while the loop was plainly working on today, which happened on 2026-08-20:
      // flies iterated from the open and took its first position at 10:52.
      dates: db
        .prepare<string[], { d: string }>(
          // The inner columns keep their own names: the era scope below filters on `trade_date`
          // and `symbol`, so renaming either inside the subquery puts them out of its reach.
          `SELECT DISTINCT trade_date AS d FROM (
             SELECT trade_date, symbol FROM fly_positions
             UNION
             SELECT trade_date, symbol FROM fly_iterations
           ) WHERE 1=1${scope} ORDER BY trade_date DESC`,
        )
        .all(...params)
        .map((r) => r.d),
      symbols: db
        .prepare<string[], { s: string }>(
          `SELECT DISTINCT symbol AS s FROM fly_positions WHERE symbol IS NOT NULL${scope} ORDER BY symbol`,
        )
        .all(...params)
        .map((r) => r.s),
    }),
  );
}

/** The profit forest: per-arm book payoff curves for the latest (or given) day. */
export function readFliesForest(
  config: ConsoleConfig,
  mode: TradingMode,
  day: string | null,
  arm: string | null,
): FliesForest {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesForest = { mode, tradeDate: null, symbol: null, arms: [], settlement: null, lastTickSpot: null };
  return withReadOnlyDb<FliesForest>(dbPath, empty, (db) => {
    const tradeDate =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions").get()?.d ?? null;
    if (tradeDate === null) return empty;

    const settleRow = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT settlement_price, settlement_source FROM fly_books
          WHERE trade_date = ? AND settlement_price IS NOT NULL LIMIT 1`,
      )
      .get(tradeDate);
    const settlement =
      typeof settleRow?.["settlement_price"] === "number"
        ? {
            price: settleRow["settlement_price"],
            source:
              typeof settleRow["settlement_source"] === "string" && settleRow["settlement_source"] !== ""
                ? settleRow["settlement_source"]
                : null,
          }
        : null;
    const lastTick = db
      .prepare<[string], { spot: number | null }>(
        "SELECT underlying_price AS spot FROM fly_iterations WHERE trade_date = ? AND underlying_price IS NOT NULL ORDER BY iteration_ts DESC LIMIT 1",
      )
      .get(tradeDate);
    const armClause = arm !== null ? " AND arm = ?" : "";
    const params: string[] = arm !== null ? [tradeDate, arm] : [tradeDate];
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, kind, side, center, wing_width, far_width, net, quantity, fees, status
           FROM fly_positions WHERE trade_date = ? AND status != 'voided' AND void_reason IS NULL${armClause}`,
      )
      .all(...params);
    const byArm = new Map<string, FlyPosition[]>();
    for (const r of rows) {
      const arm = String(r["arm"] ?? "?");
      let list = byArm.get(arm);
      if (list === undefined) {
        list = [];
        byArm.set(arm, list);
      }
      list.push({
        kind: String(r["kind"] ?? "fly"),
        side: String(r["side"] ?? "put"),
        center: Number(r["center"]),
        wingWidth: Number(r["wing_width"]),
        farWidth: typeof r["far_width"] === "number" ? r["far_width"] : null,
        net: Number(r["net"] ?? 0),
        quantity: Number(r["quantity"] ?? 1),
        fees: Number(r["fees"] ?? 0),
        status: r["status"] === null ? null : String(r["status"]),
      });
    }
    const symRow = db
      .prepare<string[], { symbol: string | null }>(
        `SELECT symbol FROM fly_positions
          WHERE trade_date = ? AND status != 'voided' AND void_reason IS NULL${armClause}
          GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 1`,
      )
      .get(...params);
    return {
      mode,
      tradeDate,
      symbol: typeof symRow?.symbol === "string" && symRow.symbol !== "" ? symRow.symbol : null,
      arms: [...byArm.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([arm, positions]) => ({ arm, curve: payoffCurve(positions) })),
      settlement,
      lastTickSpot: lastTick?.spot ?? null,
    };
  });
}

export interface TimelineTick {
  ts: string;
  spot: number | null;
  centers: Record<string, number>;
  settleNow: Record<string, number>;
}

export interface TimelineEvent {
  kind: "entry" | "completion";
  ts: string;
  arm: string;
  center: number | null;
  spot: number | null;
  structure: string;
}

export interface TimelineSpan {
  arm: string;
  center: number | null;
  from: string;
  to: string;
  latencyMin: number | null;
}

export interface FliesTimeline {
  mode: TradingMode;
  date: string | null;
  arms: string[];
  ticks: TimelineTick[];
  events: TimelineEvent[];
  spans: TimelineSpan[];
  waiting: Array<{ arm: string; center: number | null; from: string }>;
  feed: Array<{ ts: string; status: string }>;
  feedSummary: { total: number; ok: number; refusals: Record<string, number> };
}

/**
 * Port of analytics.session_timeline: the session along a TIME axis. ticks =
 * spot + each arm's wanted centre per iteration with the book replayed at each
 * tick via stateAt (an expiry payoff at a live spot, NOT a mark); events =
 * entries/completions; spans = leg-in → completion; waiting = credit spreads
 * still carrying full defined risk; feed = per-tick snapshot statuses so a
 * silence can name its reason.
 */
export function readFliesTimeline(config: ConsoleConfig, mode: TradingMode, day: string | null): FliesTimeline {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  // Memoised on the ledger's stamp: this replays every position at every recorded tick (~140ms for
  // a session), and the page re-polls every 30s while the ledger only changes on a 15s write --
  // and not at all outside a session, which is when the timeline is most often read.
  return memoOnStore(dbPath, `flies-timeline:${mode}:${day ?? "latest"}`, () =>
    buildFliesTimeline(dbPath, mode, day),
  );
}

function buildFliesTimeline(dbPath: string, mode: TradingMode, day: string | null): FliesTimeline {
  const empty: FliesTimeline = {
    mode,
    date: null,
    arms: [],
    ticks: [],
    events: [],
    spans: [],
    waiting: [],
    feed: [],
    feedSummary: { total: 0, ok: 0, refusals: {} },
  };
  return withReadOnlyDb<FliesTimeline>(dbPath, empty, (db) => {
    const date =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_iterations").get()?.d ?? null;
    if (date === null) return empty;

    interface TimelineRow extends FlyRow {
      arm: string;
      spotAtCompletion: number | null;
      underlyingAtEntry: number | null;
      latencyMin: number | null;
    }
    const rows: TimelineRow[] = db
      .prepare<[string], Record<string, unknown>>("SELECT * FROM fly_positions WHERE trade_date = ? ORDER BY entry_time")
      .all(date)
      .map((r): TimelineRow => ({
        kind: String(r["kind"] ?? "fly"),
        side: String(r["side"] ?? "put"),
        center: Number(r["center"]),
        wingWidth: Number(r["wing_width"]),
        farWidth: typeof r["far_width"] === "number" ? r["far_width"] : null,
        net: Number(r["net"] ?? 0),
        quantity: Number(r["quantity"] ?? 1),
        fees: Number(r["fees"] ?? 0),
        status: r["status"] === null ? null : String(r["status"]),
        symbol: String(r["symbol"] ?? "XSP"),
        entryTime: r["entry_time"] === null ? null : String(r["entry_time"]),
        completedAt: r["completed_at"] === null ? null : String(r["completed_at"]),
        entryMode: r["entry_mode"] === null ? null : String(r["entry_mode"]),
        credit: typeof r["credit"] === "number" ? r["credit"] : null,
        debit: typeof r["debit"] === "number" ? r["debit"] : null,
        arm: String(r["arm"] ?? "?"),
        spotAtCompletion: typeof r["spot_at_completion"] === "number" ? r["spot_at_completion"] : null,
        underlyingAtEntry: typeof r["underlying_at_entry"] === "number" ? r["underlying_at_entry"] : null,
        latencyMin: typeof r["completion_latency_min"] === "number" ? r["completion_latency_min"] : null,
      }));

    const iterations = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT iteration_ts, arm, center, underlying_price FROM fly_iterations WHERE trade_date = ? ORDER BY iteration_ts",
      )
      .all(date);

    const arms = [...new Set([...rows.map((r) => r.arm), ...iterations.map((r) => String(r["arm"] ?? ""))])]
      .filter((a) => a !== "")
      .sort();
    const byArm = new Map<string, FlyRow[]>();
    for (const r of rows) {
      let list = byArm.get(r.arm);
      if (list === undefined) {
        list = [];
        byArm.set(r.arm, list);
      }
      list.push(r);
    }

    const grouped = new Map<string, Array<Record<string, unknown>>>();
    for (const it of iterations) {
      const ts = String(it["iteration_ts"]);
      let list = grouped.get(ts);
      if (list === undefined) {
        list = [];
        grouped.set(ts, list);
      }
      list.push(it);
    }

    const ticks: TimelineTick[] = [];
    for (const ts of [...grouped.keys()].sort()) {
      const entries = grouped.get(ts)!;
      const spot = entries.map((e) => e["underlying_price"]).find((v): v is number => typeof v === "number") ?? null;
      const centers: Record<string, number> = {};
      for (const e of entries) {
        if (typeof e["center"] === "number") centers[String(e["arm"])] = e["center"];
      }
      const settleNow: Record<string, number> = {};
      if (spot !== null) {
        for (const arm of arms) {
          const states = (byArm.get(arm) ?? [])
            .map((p) => stateAt(p, ts))
            .filter((s): s is FlyPosition => s !== null);
          if (states.length > 0) settleNow[arm] = Math.round(bookPnl(states, spot) * 100) / 100;
        }
      }
      ticks.push({ ts, spot, centers, settleNow });
    }

    const events: TimelineEvent[] = [];
    const spans: TimelineSpan[] = [];
    const waiting: FliesTimeline["waiting"] = [];
    for (const r of rows) {
      if (r.entryTime !== null) {
        events.push({
          kind: "entry",
          ts: r.entryTime,
          arm: r.arm,
          center: r.center,
          spot: r.underlyingAtEntry,
          structure: r.entryMode === "legged" ? `short ${r.side}` : r.entryMode ?? "fly",
        });
      }
      if (r.completedAt !== null) {
        events.push({
          kind: "completion",
          ts: r.completedAt,
          arm: r.arm,
          center: r.center,
          spot: r.spotAtCompletion,
          structure: r.kind === "iron_fly" ? "iron fly" : "fly",
        });
        if (r.entryTime !== null) {
          spans.push({ arm: r.arm, center: r.center, from: r.entryTime, to: r.completedAt, latencyMin: r.latencyMin });
        }
      } else if (r.entryMode === "legged" && r.entryTime !== null) {
        waiting.push({ arm: r.arm, center: r.center, from: r.entryTime });
      }
    }
    events.sort((a, b) => a.ts.localeCompare(b.ts));

    const feedRows = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT iteration_ts, status FROM fly_snapshots WHERE trade_date = ? ORDER BY iteration_ts",
      )
      .all(date)
      .map((r) => ({ ts: String(r["iteration_ts"]), status: String(r["status"] ?? "?") }));
    const refusals: Record<string, number> = {};
    let ok = 0;
    for (const f of feedRows) {
      if (f.status === "ok") ok += 1;
      else refusals[f.status] = (refusals[f.status] ?? 0) + 1;
    }

    return {
      mode,
      date,
      arms,
      ticks,
      events,
      spans,
      waiting,
      feed: feedRows,
      feedSummary: { total: feedRows.length, ok, refusals },
    };
  });
}

export interface JournalRow {
  arm: string;
  mode: string;
  reason: string;
  accepted: boolean;
  firstSeen: string | null;
  lastSeen: string | null;
  occurrences: number;
  centerLast: number | null;
  detail: string | null;
}

/** The day's decisions, newest run first — already collapsed at write time, so a plain read. */
export function readFliesJournal(config: ConsoleConfig, mode: TradingMode, day: string | null, arm: string | null): { date: string | null; rows: JournalRow[] } {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<{ date: string | null; rows: JournalRow[] }>(dbPath, { date: null, rows: [] }, (db) => {
    const date =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_decisions").get()?.d ?? null;
    if (date === null) return { date: null, rows: [] };
    const clause = arm !== null ? " AND arm = ?" : "";
    const params: string[] = arm !== null ? [date, arm] : [date];
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, mode, reason, accepted, first_seen, last_seen, occurrences, center_last, detail
           FROM fly_decisions WHERE trade_date = ?${clause} ORDER BY id DESC`,
      )
      .all(...params)
      .map((r) => ({
        arm: String(r["arm"] ?? "?"),
        mode: String(r["mode"] ?? "?"),
        reason: String(r["reason"] ?? ""),
        accepted: r["accepted"] === 1,
        firstSeen: str(r["first_seen"]),
        lastSeen: str(r["last_seen"]),
        occurrences: Number(r["occurrences"] ?? 1),
        centerLast: num(r["center_last"]),
        detail: str(r["detail"]),
      }));
    return { date, rows };
  });
}

// ---- history / performance (ports of analytics.py's read layer) ----

/** Settled, non-void — the shared WHERE every history read builds on. */
const SETTLED = "status = 'settled' AND void_reason IS NULL";

export interface FliesSummary {
  trades: number;
  /** Distinct settled sessions behind `trades` — the unit of independence. */
  sessions: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  wins: number;
  losses: number;
  winRatePct: number | null;
  avgPnl: number | null;
  feeDragPct: number | null;
  profitFactor: number | null;
}

interface PnlRow {
  gross: number;
  fees: number;
  pnl: number;
  /** Carried so every summary can report SESSIONS beside trades. Same-day trades share a regime, so
   *  they are not independent observations — this module's own experiment docs put the effective N
   *  at the day count, and a per-arm net over 40 trades from 3 sessions is a 3-sample reading
   *  wearing a 40-sample coat. */
  day: string;
}

function summarize(rows: PnlRow[]): FliesSummary {
  const sessions = new Set(rows.map((r) => r.day).filter((d) => d !== "")).size;
  const gross = rows.reduce((s, r) => s + r.gross, 0);
  const fees = rows.reduce((s, r) => s + r.fees, 0);
  const nets = rows.map((r) => r.pnl);
  const wins = nets.filter((n) => n > 0);
  const losses = nets.filter((n) => n < 0);
  const totalWin = wins.reduce((s, n) => s + n, 0);
  const totalLoss = Math.abs(losses.reduce((s, n) => s + n, 0));
  const net = nets.reduce((s, n) => s + n, 0);
  return {
    trades: rows.length,
    sessions,
    grossPnl: gross,
    fees,
    netPnl: net,
    wins: wins.length,
    losses: losses.length,
    winRatePct: wins.length + losses.length > 0 ? (wins.length / (wins.length + losses.length)) * 100 : null,
    avgPnl: nets.length > 0 ? net / nets.length : null,
    feeDragPct: gross > 0 ? (fees / gross) * 100 : null,
    profitFactor: totalLoss > 0 ? totalWin / totalLoss : null,
  };
}

function pnlRows(db: import("better-sqlite3").Database, where: string, params: string[]): Array<Record<string, unknown>> {
  return db
    .prepare<string[], Record<string, unknown>>(
      `SELECT trade_date, arm, entry_mode, entry_window, COALESCE(gross_pnl, 0) AS gross,
              COALESCE(fees, 0) AS fees, COALESCE(pnl, 0) AS pnl
         FROM fly_positions WHERE ${where}`,
    )
    .all(...params);
}

const toPnl = (r: Record<string, unknown>): PnlRow => ({
  gross: Number(r["gross"]),
  fees: Number(r["fees"]),
  pnl: Number(r["pnl"]),
  day: String(r["trade_date"] ?? ""),
});

function groupSummaries<T extends string>(rows: Array<Record<string, unknown>>, key: string, label: T): Array<Record<T, string> & FliesSummary> {
  const grouped = new Map<string, PnlRow[]>();
  for (const r of rows) {
    const k = String(r[key] ?? "unknown");
    let list = grouped.get(k);
    if (list === undefined) {
      list = [];
      grouped.set(k, list);
    }
    list.push(toPnl(r));
  }
  return [...grouped.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([k, rs]) => ({ [label]: k, ...summarize(rs) }) as Record<T, string> & FliesSummary);
}

export interface ArmDivergence {
  date: string | null;
  iterations: number;
  allAgreeRatePct: number | null;
  pairs: Array<{ arms: string; iterations: number; agreementRatePct: number | null }>;
}

/**
 * Port of analytics.arm_divergence: how often the arms actually picked
 * DIFFERENT centres. The experiment can only separate two arms to the extent
 * they disagree — a high agreement rate means the comparison cannot answer
 * the question as framed, and that is far better learned in week one.
 */
export function readArmDivergence(config: ConsoleConfig, mode: TradingMode, day: string | null): ArmDivergence {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: ArmDivergence = { date: null, iterations: 0, allAgreeRatePct: null, pairs: [] };
  return withReadOnlyDb<ArmDivergence>(dbPath, empty, (db) => {
    const date =
      day ?? db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_iterations").get()?.d ?? null;
    if (date === null) return empty;
    const rows = db
      .prepare<[string], Record<string, unknown>>(
        "SELECT iteration_ts, symbol, arm, center FROM fly_iterations WHERE trade_date = ? ORDER BY iteration_ts",
      )
      .all(date);
    const iterations = new Map<string, Record<string, number>>();
    for (const r of rows) {
      if (typeof r["center"] !== "number") continue;
      const key = `${String(r["iteration_ts"])}|${String(r["symbol"])}`;
      let bucket = iterations.get(key);
      if (bucket === undefined) {
        bucket = {};
        iterations.set(key, bucket);
      }
      bucket[String(r["arm"])] = r["center"];
    }
    const pairs = new Map<string, boolean[]>();
    let allAgree = 0;
    let considered = 0;
    for (const centers of iterations.values()) {
      const arms = Object.keys(centers).sort();
      if (arms.length < 2) continue;
      considered += 1;
      if (new Set(Object.values(centers)).size === 1) allAgree += 1;
      for (let i = 0; i < arms.length; i++) {
        for (let j = i + 1; j < arms.length; j++) {
          const key = `${arms[i]} vs ${arms[j]}`;
          let list = pairs.get(key);
          if (list === undefined) {
            list = [];
            pairs.set(key, list);
          }
          list.push(centers[arms[i]!] === centers[arms[j]!]);
        }
      }
    }
    return {
      date,
      iterations: considered,
      allAgreeRatePct: considered > 0 ? (allAgree / considered) * 100 : null,
      pairs: [...pairs.entries()]
        .map(([arms, matches]) => ({
          arms,
          iterations: matches.length,
          agreementRatePct: matches.length > 0 ? (matches.filter(Boolean).length / matches.length) * 100 : null,
        }))
        .sort((a, b) => (b.agreementRatePct ?? 0) - (a.agreementRatePct ?? 0)),
    };
  });
}

export interface FliesHistory {
  mode: TradingMode;
  /** Legged-only, per the reference: the arms differ by centring/timing/width, never by entry mode. */
  byArm: Array<{ arm: string } & FliesSummary>;
  byEntryMode: Array<{ entryMode: string } & FliesSummary>;
  byEntryWindow: Array<{ window: string } & FliesSummary>;
  feeDrag: Array<{ arm: string } & FliesSummary>;
  dailyPnl: Array<{ date: string; trades: number; netPnl: number }>;
}

export interface FliesTradeLogRow {
  tradeDate: string;
  symbol: string;
  arm: string | null;
  entryMode: string | null;
  kind: string | null;
  side: string | null;
  center: number | null;
  window: string | null;
  net: number | null;
  fees: number | null;
  pnl: number | null;
  latencyMin: number | null;
  pinned: boolean;
}

export type FliesOutcome = "all" | "wins" | "losses" | "pinned" | "risk-free";

export interface FliesTradeLogQuery extends PageRequest {
  outcome: FliesOutcome;
  search: string;
}

export const NO_TRADE_LOG_QUERY: FliesTradeLogQuery = { ...FIRST_PAGE, outcome: "all", search: "" };

/**
 * The settled trade log, filtered and paged in SQL. It lives apart from
 * `readFliesHistory` so turning a page costs one indexed query instead of
 * recomputing every summary on the tab.
 */
export function readFliesTradeLog(
  config: ConsoleConfig,
  mode: TradingMode,
  query: FliesTradeLogQuery = NO_TRADE_LOG_QUERY,
): Paged<FliesTradeLogRow> {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<Paged<FliesTradeLogRow>>(dbPath, emptyPage(query), (db) => {
    const clauses = [SETTLED];
    const params: string[] = [];
    if (query.outcome === "wins") clauses.push("pnl IS NOT NULL AND pnl > 0");
    if (query.outcome === "losses") clauses.push("pnl IS NOT NULL AND pnl < 0");
    if (query.outcome === "pinned") clauses.push("pinned = 1");
    // Risk-free is the fly that came back whole: a butterfly closed at or above
    // flat, the shape the module is built to manufacture.
    if (query.outcome === "risk-free") clauses.push("pnl IS NOT NULL AND pnl >= 0 AND kind = 'fly'");
    if (query.search !== "") {
      clauses.push(
        `(trade_date LIKE ? OR symbol LIKE ? OR COALESCE(arm, '') LIKE ? OR COALESCE(entry_mode, '') LIKE ?
          OR COALESCE(kind, '') LIKE ? OR COALESCE(entry_window, '') LIKE ?)`,
      );
      const like = `%${query.search.replace(/[%_]/g, "")}%`;
      params.push(like, like, like, like, like, like);
    }
    return pagedQuery<FliesTradeLogRow>(
      db,
      {
        columns: `trade_date, symbol, arm, entry_mode, kind, side, center, entry_window, net, fees, pnl,
                  completion_latency_min, pinned`,
        from: "fly_positions",
        where: clauses.join(" AND "),
        params,
        orderBy: "trade_date DESC, entry_time DESC",
      },
      query,
      (r) => ({
        tradeDate: String(r["trade_date"]),
        symbol: String(r["symbol"] ?? ""),
        arm: str(r["arm"]),
        entryMode: str(r["entry_mode"]),
        kind: str(r["kind"]),
        side: str(r["side"]),
        center: num(r["center"]),
        window: str(r["entry_window"]),
        net: num(r["net"]),
        fees: num(r["fees"]),
        pnl: num(r["pnl"]),
        latencyMin: num(r["completion_latency_min"]),
        pinned: r["pinned"] === 1,
      }),
    );
  });
}

/**
 * The multi-day views' scope: arm, symbol and era — deliberately NOT date.
 *
 * History and Performance answer questions ACROSS sessions, so pinning a day would empty them.
 * They are otherwise narrowed exactly like the day views, and by the same era default: a per-arm
 * ranking or an equity curve that silently pools the XSP books against the SPX ones is comparing
 * two different trades at 5x different width and 4x different fee drag.
 */
function scopeClause(filter: FliesFilter): { and: string; params: string[] } {
  const clauses: string[] = [];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  if (filter.symbol !== null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }
  const era = eraClause(filter.era);
  if (era.sql !== null) {
    clauses.push(era.sql);
    params.push(...era.params);
  }
  return { and: clauses.length > 0 ? ` AND ${clauses.join(" AND ")}` : "", params };
}

export function readFliesHistory(
  config: ConsoleConfig,
  mode: TradingMode,
  filter: FliesFilter,
): FliesHistory {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesHistory = { mode, byArm: [], byEntryMode: [], byEntryWindow: [], feeDrag: [], dailyPnl: [] };
  return withReadOnlyDb<FliesHistory>(dbPath, empty, (db) => {
    const sc = scopeClause(filter);
    const legged = pnlRows(db, `${SETTLED} AND entry_mode = 'legged'${sc.and}`, sc.params);
    const all = pnlRows(db, `${SETTLED}${sc.and}`, sc.params);
    // Sorted by NAME, not by net. A leaderboard over 3-8 sessions manufactures a ranking out of
    // noise, and on 2026-08-11 it would have put a two-position arm on top -- the same mistake the
    // EOD debrief made that day. The arms are deliberately-different single-variable twins, so the
    // useful reading is a pair against its baseline, not a league table. Flies already states this
    // discipline on "By entry window (deliberately unranked)"; this is the same call.
    const byArm = groupSummaries(legged, "arm", "arm").sort((a, b) => a.arm.localeCompare(b.arm));

    const dailyMap = new Map<string, PnlRow[]>();
    for (const r of all) {
      const d = String(r["trade_date"]);
      let list = dailyMap.get(d);
      if (list === undefined) {
        list = [];
        dailyMap.set(d, list);
      }
      list.push(toPnl(r));
    }
    const dailyPnl = [...dailyMap.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, rs]) => ({ date, trades: rs.length, netPnl: rs.reduce((s, x) => s + x.pnl, 0) }));

    return {
      mode,
      byArm,
      byEntryMode: groupSummaries(all, "entry_mode", "entryMode"),
      byEntryWindow: groupSummaries(all, "entry_window", "window"),
      feeDrag: byArm,
      dailyPnl,
    };
  });
}

/** One leg-in-then-convert story: entries, conversions, why the misses missed, and how long the
 *  conversions took. Shared by the legged completion and the bwb roll so the two can never drift
 *  into reporting the same idea differently. */
export interface CompletionBlock {
  leggedEntries: number;
  completed: number;
  completionRatePct: number | null;
  neverOffered: number;
  bufferBlocked: number;
  floorBlocked: number;
  unknown: number;
  medianLatencyMin: number | null;
  minLatencyMin: number | null;
  maxLatencyMin: number | null;
  medianSpotMove: number | null;
}

/** One bucket's (or the whole scope's) improvement summary -- `analytics.left_on_table`'s
 *  `summarize()`, mirrored exactly. */
export interface LeftOnTableSummary {
  n: number;
  improved: number;
  medianImprovementPts: number | null;
  maxImprovementPts: number | null;
  medianImprovementDollars: number | null;
  totalImprovementDollars: number | null;
}

/** How much better the completing price got AFTER the first qualifying tick was taken -- the
 *  counterfactual behind debit-first's wait-for-better completion rule. Mirrors
 *  `cherrypick.flies.analytics.left_on_table` exactly (same columns, same floor-at-zero, same
 *  gex-bucket split); this package can't import that Python function, so the query is
 *  reimplemented here rather than shelled out to it. Null when the scope holds no debit_first
 *  completions, same convention as `roll`. */
export interface LeftOnTable {
  entryMode: "debit_first";
  untracked: number;
  overall: LeftOnTableSummary;
  byGexBucket: Record<string, LeftOnTableSummary>;
}

export interface FliesPerformance {
  mode: TradingMode;
  tiles: FliesSummary & { completionRatePct: number | null };
  series: Array<{ bucket: string; netPnl: number; cumulative: number }>;
  /**
   * The bwb arm's equivalent of `completion`, and it needs its own block rather than a row in that
   * one: a bwb is entered WHOLE for a credit and converted by a ROLL, not legged in and completed,
   * so "completion rate" is not a number it has. Null when the scope holds no bwb_roll entries.
   *
   * Same counterfactual split, on the roll's own columns (`best_roll_debit` against the entry
   * `credit`): "the market never made the roll cheap enough" and "our own gate refused it" are
   * identical in the P&L and call for opposite fixes, which is why this module reports them apart
   * everywhere else too.
   */
  roll: CompletionBlock | null;
  leftOnTable: LeftOnTable | null;
  completion: {
    leggedEntries: number;
    completed: number;
    completionRatePct: number | null;
    neverOffered: number;
    bufferBlocked: number;
    floorBlocked: number;
    unknown: number;
    medianLatencyMin: number | null;
    minLatencyMin: number | null;
    maxLatencyMin: number | null;
    medianSpotMove: number | null;
  };
  completionTrend: Array<{ day: string; legged: number; completed: number; ratePct: number | null }>;
  rollTrend: Array<{ day: string; legged: number; completed: number; ratePct: number | null }>;
  liveVsPaper: {
    arm: string;
    live: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    paper: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    completionGapPct: number | null;
    abort: { minLiveEntries: number; gapLimitPct: number; armed: boolean; triggered: boolean };
  } | null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1 ? ordered[mid]! : (ordered[mid - 1]! + ordered[mid]!) / 2;
}

/** Weekly buckets are Monday-anchored (SQLite's %W would split a trading week). */
function bucketKey(tradeDate: string, granularity: string): string {
  if (granularity === "monthly") return tradeDate.slice(0, 7);
  if (granularity === "weekly") {
    const d = new Date(tradeDate + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  return tradeDate;
}

const ABORT_MIN_LIVE_ENTRIES = 30;
const ABORT_COMPLETION_GAP = 0.15;

export function readFliesPerformance(
  config: ConsoleConfig,
  mode: TradingMode,
  granularity: string,
  filter: FliesFilter,
): FliesPerformance {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesPerformance = {
    mode,
    tiles: { ...summarize([]), completionRatePct: null },
    series: [],
    completion: {
      leggedEntries: 0, completed: 0, completionRatePct: null, neverOffered: 0, bufferBlocked: 0,
      floorBlocked: 0, unknown: 0, medianLatencyMin: null, minLatencyMin: null, maxLatencyMin: null, medianSpotMove: null,
    },
    roll: null,
    leftOnTable: null,
    completionTrend: [],
    rollTrend: [],
    liveVsPaper: null,
  };
  const result = withReadOnlyDb<FliesPerformance>(dbPath, empty, (db) => {
    const sc = scopeClause(filter);
    const all = pnlRows(db, `${SETTLED}${sc.and}`, sc.params);
    const tilesBase = summarize(all.map(toPnl));

    const buckets = new Map<string, PnlRow[]>();
    for (const r of all) {
      const k = bucketKey(String(r["trade_date"]), granularity);
      let list = buckets.get(k);
      if (list === undefined) {
        list = [];
        buckets.set(k, list);
      }
      list.push(toPnl(r));
    }
    let cumulative = 0;
    const series = [...buckets.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([bucket, rs]) => {
        const net = rs.reduce((s, x) => s + x.pnl, 0);
        cumulative += net;
        return { bucket, netPnl: net, cumulative };
      });

    // completion_stats, legged: the counterfactual's floor split reads the
    // decisions journal (the engine's own recorded reason), never recomputed.
    // One computation, run per entry mode. `legged` completes by BUYING the completing debit spread;
    // `bwb_roll` converts by ROLLING the far wing in. Different trades, identical question -- how
    // often did the conversion happen, why did the misses miss, and how long did the winners take --
    // so they share the code rather than growing two versions that quietly answer it differently.
    const conversionSpec = {
      legged: {
        best: "best_completing_debit",
        latency: "completion_latency_min",
        spot: "spot_at_completion",
        gateReason: "floor_below_minimum_after_fees",
        // Cheaper than the credit is the direction that means "the market offered it".
        offered: (best: number, target: number) => best < target,
      },
      bwb_roll: {
        best: "best_roll_debit",
        latency: "roll_latency_min",
        spot: "spot_at_roll",
        gateReason: "floor_below_minimum_after_fees",
        offered: (best: number, target: number) => best < target,
      },
    } as const;

    const floorGated = new Set(
      db
        .prepare<[], { position_id: string | null }>(
          "SELECT DISTINCT position_id FROM fly_decisions WHERE mode = 'completion' AND reason = 'floor_below_minimum_after_fees'",
        )
        .all()
        .map((r) => r.position_id)
        .filter((p): p is string => p !== null),
    );
    // Voided rows are EXCLUDED, and for the roll that is not a detail: 25 of the 37 bwb entries in
    // the ledger carry a void_reason, because evaluate_roll priced the wrong legs until 2026-08-07
    // (a spread of width far+wing instead of far-wing, 3x too wide at the default ratio). Counting
    // them put the roll rate at 65% when the rows that were actually the trade give 83%. The module
    // disavowed those decisions; a read surface that quietly includes them republishes them.
    const conversionFor = (mode: keyof typeof conversionSpec): CompletionBlock => {
      const spec = conversionSpec[mode];
      const rows = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT position_id, kind, credit, ${spec.best} AS best, ${spec.latency} AS latency,
                  underlying_at_entry, ${spec.spot} AS spot_at_done
             FROM fly_positions WHERE entry_mode = ? AND void_reason IS NULL${sc.and}`,
        )
        .all(mode, ...sc.params);
      // 'fly' is the converted kind for BOTH modes: a completed leg-in and a rolled bwb both end up
      // holding a symmetric butterfly, which is the point of each.
      const done = rows.filter((r) => r["kind"] === "fly");
      const missed = rows.filter((r) => r["kind"] !== "fly");
      let neverOffered = 0;
      let bufferBlocked = 0;
      let floorBlocked = 0;
      let unknown = 0;
      for (const r of missed) {
        const best = num(r["best"]);
        const target = num(r["credit"]);
        if (best === null || target === null) unknown += 1;
        else if (!spec.offered(best, target)) neverOffered += 1;
        else if (floorGated.has(String(r["position_id"]))) floorBlocked += 1;
        else bufferBlocked += 1;
      }
      const lats = done.map((r) => num(r["latency"])).filter((v): v is number => v !== null);
      const mvs = done
        .map((r) => {
          const a = num(r["spot_at_done"]);
          const b = num(r["underlying_at_entry"]);
          return a !== null && b !== null ? Math.abs(a - b) : null;
        })
        .filter((v): v is number => v !== null);
      return {
        leggedEntries: rows.length,
        completed: done.length,
        completionRatePct: rows.length > 0 ? (done.length / rows.length) * 100 : null,
        neverOffered,
        bufferBlocked,
        floorBlocked,
        unknown,
        medianLatencyMin: median(lats),
        minLatencyMin: lats.length > 0 ? Math.min(...lats) : null,
        maxLatencyMin: lats.length > 0 ? Math.max(...lats) : null,
        medianSpotMove: median(mvs),
      };
    };

    const legged = conversionFor("legged");
    const rollBlock = conversionFor("bwb_roll");

    const round = (v: number | null, digits = 2): number | null => (v === null ? null : Math.round(v * 10 ** digits) / 10 ** digits);

    const summarizeImprovement = (pairs: Array<{ pts: number; dollars: number }>): LeftOnTableSummary => {
      const pts = pairs.map((p) => p.pts);
      const dollars = pairs.map((p) => p.dollars);
      return {
        n: pairs.length,
        improved: pts.filter((p) => p > 0).length,
        medianImprovementPts: round(median(pts), 4),
        maxImprovementPts: pts.length > 0 ? round(Math.max(...pts), 4) : null,
        medianImprovementDollars: round(median(dollars)),
        totalImprovementDollars: round(dollars.reduce((s, v) => s + v, 0)),
      };
    };

    // Only debit_first completions carry the wait-for-better hypothesis this measures -- dealer
    // pinning (positive-gamma pull toward the centre) is the regime where waiting should have paid,
    // so the split by completion_gex_bucket is the whole point, not an afterthought.
    const leftOnTableRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT credit, debit, quantity, completion_gex_bucket, post_best_completing_debit, post_best_completing_credit
           FROM fly_positions WHERE entry_mode = 'debit_first' AND kind = 'fly'${sc.and}`,
      )
      .all(...sc.params);
    let leftOnTableUntracked = 0;
    const leftOnTableAll: Array<{ pts: number; dollars: number }> = [];
    const leftOnTableByBucket = new Map<string, Array<{ pts: number; dollars: number }>>();
    for (const r of leftOnTableRows) {
      const postCredit = num(r["post_best_completing_credit"]);
      const credit = num(r["credit"]);
      if (postCredit === null || credit === null) {
        leftOnTableUntracked += 1;
        continue;
      }
      const pts = Math.max(0, postCredit - credit);
      const qty = num(r["quantity"]) ?? 1;
      const pair = { pts, dollars: pts * 100 * qty };
      leftOnTableAll.push(pair);
      const bucket = str(r["completion_gex_bucket"]) ?? "untagged";
      const list = leftOnTableByBucket.get(bucket);
      if (list === undefined) leftOnTableByBucket.set(bucket, [pair]);
      else list.push(pair);
    }
    const leftOnTable: LeftOnTable | null =
      leftOnTableRows.length > 0
        ? {
            entryMode: "debit_first",
            untracked: leftOnTableUntracked,
            overall: summarizeImprovement(leftOnTableAll),
            byGexBucket: Object.fromEntries(
              [...leftOnTableByBucket.entries()]
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([bucket, pairs]) => [bucket, summarizeImprovement(pairs)]),
            ),
          }
        : null;

    const trendFor = (mode: string) =>
      db
        .prepare<string[], Record<string, unknown>>(
          `SELECT trade_date, COUNT(*) AS legged, SUM(CASE WHEN kind = 'fly' THEN 1 ELSE 0 END) AS completed
             FROM fly_positions WHERE entry_mode = ? AND void_reason IS NULL${sc.and}
              GROUP BY trade_date ORDER BY trade_date`,
        )
        .all(mode, ...sc.params)
        .map((r) => {
          const n = Number(r["legged"]);
          const c = Number(r["completed"]);
          return { day: String(r["trade_date"]), legged: n, completed: c, ratePct: n > 0 ? (c / n) * 100 : null };
        });

    return {
      mode,
      tiles: {
        ...tilesBase,
        completionRatePct: legged.completionRatePct,
      },
      series,
      completion: legged,
      // Null rather than a block of zeros when the scope holds no bwb entries: an empty panel that
      // says "0% roll rate" is a claim, and there is nothing to claim.
      roll: rollBlock.leggedEntries > 0 ? rollBlock : null,
      leftOnTable,
      completionTrend: trendFor("legged"),
      rollTrend: trendFor("bwb_roll"),
      liveVsPaper: null,
    };
  });

  // live vs paper: CONTEMPORANEOUS — paper restricted to the live arm's sessions.
  result.liveVsPaper = withReadOnlyDb(path.join(config.paths.fliesDir, "live_trades.db"), null, (liveDb) => {
    const days = liveDb
      .prepare<[], { d: string }>(
        "SELECT DISTINCT trade_date AS d FROM fly_positions WHERE arm = 'gex' AND entry_mode = 'legged' AND status != 'cancelled' ORDER BY trade_date",
      )
      .all()
      .map((r) => r.d);
    if (days.length === 0) return null;
    const marks = days.map(() => "?").join(",");
    const side = (db: import("better-sqlite3").Database) => {
      const rows = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT kind, credit, completion_latency_min FROM fly_positions
            WHERE arm = 'gex' AND entry_mode = 'legged' AND status != 'cancelled' AND trade_date IN (${marks})`,
        )
        .all(...days);
      const done = rows.filter((r) => r["kind"] === "fly");
      const lat = done.map((r) => num(r["completion_latency_min"])).filter((v): v is number => v !== null && v !== 0);
      const credits = rows.map((r) => num(r["credit"])).filter((v): v is number => v !== null);
      return {
        sessions: days.length,
        entries: rows.length,
        completed: done.length,
        completionRatePct: rows.length > 0 ? (done.length / rows.length) * 100 : null,
        medianLatencyMin: median(lat),
        avgCredit: credits.length > 0 ? credits.reduce((s, v) => s + v, 0) / credits.length : null,
      };
    };
    const live = side(liveDb);
    const paper = withReadOnlyDb(path.join(config.paths.fliesDir, "paper_trades.db"), side(liveDb), side);
    const gap =
      live.completionRatePct !== null && paper.completionRatePct !== null
        ? (paper.completionRatePct - live.completionRatePct) / 100
        : null;
    const armed = live.entries >= ABORT_MIN_LIVE_ENTRIES;
    return {
      arm: "gex",
      live,
      paper,
      completionGapPct: gap !== null ? gap * 100 : null,
      abort: {
        minLiveEntries: ABORT_MIN_LIVE_ENTRIES,
        gapLimitPct: ABORT_COMPLETION_GAP * 100,
        armed,
        triggered: armed && gap !== null && gap > ABORT_COMPLETION_GAP,
      },
    };
  });

  return result;
}

export interface FliesAnalytics {
  mode: TradingMode;
  /** Latest trade date's tiles, flies-dashboard shape. */
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
    /** Every OPEN position own worst case, summed. Zero means nothing open can still lose. */
    maxPossibleLoss: number;
  };
  byArm: Array<{ arm: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  feeDrag: Array<{ arm: string; gross: number; fees: number; net: number; dragPct: number | null }>;
}

export function readFliesAnalytics(config: ConsoleConfig, mode: TradingMode, filter: FliesFilter): FliesAnalytics {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesAnalytics = {
    mode,
    today: { tradeDate: null, netPnl: 0, positions: 0, open: 0, riskFree: 0, completionPct: null, fees: 0, maxPossibleLoss: 0 },
    byArm: [],
    feeDrag: [],
  };
  return withReadOnlyDb<FliesAnalytics>(dbPath, empty, (db) => {
    const latest = db
      .prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM fly_positions")
      .get();
    const tradeDate = filter.date ?? latest?.d ?? null;
    const armClause = filter.arm !== null ? " AND arm = ?" : "";
    const armParams: string[] = filter.arm !== null ? [filter.arm] : [];

    let today = empty.today;
    if (tradeDate !== null) {
      const t = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT COALESCE(SUM(pnl), 0) AS net, COUNT(*) AS positions,
                  SUM(CASE WHEN status NOT IN ('settled','closed','voided') THEN 1 ELSE 0 END) AS open,
                  SUM(CASE WHEN risk_free = 1 THEN 1 ELSE 0 END) AS risk_free,
                  SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                  COALESCE(SUM(fees), 0) AS fees
             FROM fly_positions WHERE trade_date = ?${armClause}`,
        )
        .get(tradeDate, ...armParams) ?? {};
      const positions = Number(t["positions"] ?? 0);
      // Max possible loss: each still-open position own floor, summed — reads
      // zero when nothing open can lose any more.
      const openRows = db
        .prepare<string[], Record<string, unknown>>(
          `SELECT kind, side, center, wing_width, far_width, net, quantity, fees, status
             FROM fly_positions WHERE trade_date = ?${armClause}
              AND status NOT IN ('settled','closed','voided')`,
        )
        .all(tradeDate, ...armParams);
      const maxPossibleLoss = openRows.reduce((sum, r) => {
        const floor = positionFloor({
          kind: String(r["kind"] ?? "fly"),
          side: String(r["side"] ?? "put"),
          center: Number(r["center"]),
          wingWidth: Number(r["wing_width"]),
          farWidth: typeof r["far_width"] === "number" ? r["far_width"] : null,
          net: Number(r["net"] ?? 0),
          quantity: Number(r["quantity"] ?? 1),
          fees: Number(r["fees"] ?? 0),
          status: r["status"] === null ? null : String(r["status"]),
        });
        return sum + Math.min(0, floor);
      }, 0);
      today = {
        tradeDate,
        netPnl: Number(t["net"] ?? 0),
        positions,
        open: Number(t["open"] ?? 0),
        riskFree: Number(t["risk_free"] ?? 0),
        completionPct: positions > 0 ? (Number(t["completed"] ?? 0) / positions) * 100 : null,
        fees: Number(t["fees"] ?? 0),
        maxPossibleLoss,
      };
    }

    // The RESOLVED day, not the raw filter: these two tables sit directly under the session card on
    // the Today tab, and scoping them to "every day in the era" while that card showed one session
    // put two different questions side by side under one date select.
    const dateClause = tradeDate !== null ? " AND trade_date = ?" : "";
    const dateParams: string[] = tradeDate !== null ? [tradeDate] : [];
    const armRows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, COUNT(*) AS trades, COALESCE(SUM(pnl), 0) AS net,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) AS won,
                COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) AS lost
           FROM fly_positions WHERE pnl IS NOT NULL${dateClause}${armClause} GROUP BY arm ORDER BY net DESC`,
      )
      .all(...dateParams, ...armParams);
    const byArm = armRows.map((r) => {
      const trades = Number(r["trades"]);
      const wins = Number(r["wins"]);
      const losses = Number(r["losses"]);
      const lost = Number(r["lost"]);
      return {
        arm: String(r["arm"] ?? "?"),
        trades,
        net: Number(r["net"]),
        winPct: wins + losses > 0 ? (wins / (wins + losses)) * 100 : null,
        avg: trades > 0 ? Number(r["net"]) / trades : null,
        profitFactor: lost > 0 ? Number(r["won"]) / lost : null,
      };
    });

    const feeDrag = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT arm, COALESCE(SUM(gross_pnl), 0) AS gross, COALESCE(SUM(fees), 0) AS fees,
                COALESCE(SUM(pnl), 0) AS net
           FROM fly_positions WHERE pnl IS NOT NULL${dateClause}${armClause} GROUP BY arm ORDER BY arm`,
      )
      .all(...dateParams, ...armParams)
      .map((r) => {
        const gross = Number(r["gross"]);
        const fees = Number(r["fees"]);
        return {
          arm: String(r["arm"] ?? "?"),
          gross,
          fees,
          net: Number(r["net"]),
          dragPct: Math.abs(gross) > 0 ? (fees / Math.abs(gross)) * 100 : null,
        };
      });

    return { mode, today, byArm, feeDrag };
  });
}

// ── Loop freshness ──────────────────────────────────────────────────────────

export interface FliesLoopStatus {
  /** LIVE while the loop has written an iteration inside its own tick window, else IDLE. */
  state: "live" | "idle" | "no-data";
  lastIterationAt: string | null;
  ageSeconds: number | null;
  symbol: string | null;
  arm: string | null;
  underlyingPrice: number | null;
}

/**
 * Is the flies loop actually running?
 *
 * `fly_iterations` is the right source rather than the ledger: it records what every arm WANTED on
 * each tick, so it advances on a quiet market where positions and books do not, which is exactly
 * when "is this thing alive" is worth asking. The window is 120s against a 15s tick — several
 * cadences wide, so one slow pass is never reported as a stall.
 */
export function readFliesLoopStatus(config: ConsoleConfig, mode: TradingMode): FliesLoopStatus {
  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const empty: FliesLoopStatus = {
    state: "no-data",
    lastIterationAt: null,
    ageSeconds: null,
    symbol: null,
    arm: null,
    underlyingPrice: null,
  };
  return withReadOnlyDb<FliesLoopStatus>(dbPath, empty, (db) => {
    const r = db
      .prepare<[], Record<string, unknown>>(
        `SELECT iteration_ts, symbol, arm, underlying_price
           FROM fly_iterations ORDER BY id DESC LIMIT 1`,
      )
      .get();
    if (r === undefined) return empty;
    const lastIterationAt = str(r["iteration_ts"]);
    let ageSeconds: number | null = null;
    if (lastIterationAt !== null) {
      const t = Date.parse(lastIterationAt.includes("T") ? lastIterationAt : lastIterationAt.replace(" ", "T"));
      if (!Number.isNaN(t)) ageSeconds = Math.max(0, (Date.now() - t) / 1000);
    }
    return {
      state: ageSeconds !== null && ageSeconds < 120 ? "live" : "idle",
      lastIterationAt,
      ageSeconds,
      symbol: str(r["symbol"]),
      arm: str(r["arm"]),
      underlyingPrice: num(r["underlying_price"]),
    };
  });
}
