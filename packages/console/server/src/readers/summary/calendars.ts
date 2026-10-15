import fs from "node:fs";
import path from "node:path";
import type { CalendarsWeekRow, ModuleSummarySlice, SummaryGauge, SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readCalendarsWeeks } from "../calendars.js";
import { obj, readJson, str, withReadOnlyDb } from "../db.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  inWindow,
  sessionNeighbours,
  summaryWindow,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * Calendars' simplified-view summary.
 *
 * A trade here is one row of the module's history table: one arm's double calendar for one week
 * (the put calendar and the call calendar together). The money is that table's own
 * (`readCalendarsWeeks`, every position through `positionCash`), so the hero and the history's
 * totals are one arithmetic. A week is booked to its Monday (`week_of`) -- the history table's own
 * date -- so a "session" on this slide is a week, and a day picked mid-week snaps to its Monday.
 *
 * Paper only, structurally: there is no live ledger, so `q.mode` is reported but never read.
 *
 * Structure tags never pool (the module's fourth honesty rule): the week-type breakdown is by tag,
 * each tag named in words, never folded into one "holiday" bucket.
 */

const MODULE = "calendars";
const UNIT = "a weekly SPY double calendar";
const DB_FILE = "paper_trades.db";

const round2 = (v: number) => Math.round(v * 100) / 100;

/** The Monday of a session's week (ISO dates, UTC arithmetic). */
function mondayOf(day: string): string {
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() - ((at.getUTCDay() + 6) % 7));
  return at.toISOString().slice(0, 10);
}

/** The module's config, resolved the way the module resolves it (first readable candidate). */
function moduleConfig(config: ConsoleConfig): Record<string, unknown> | null {
  for (const candidate of config.paths.calendarsConfigCandidates ?? []) {
    const doc = readJson(candidate);
    if (doc !== null) return doc;
  }
  return null;
}

/**
 * Retired arms, by the console's rule (`enabled: false` in the module's own registry, read under
 * any of the suite's spellings). The Friday-entry regime's arms are its base roster under a
 * `friday:` prefix, so they are retired too while that regime is switched off. `readRetiredArms`
 * does not cover calendars, so this reads the registry itself.
 */
function retiredArms(doc: Record<string, unknown> | null): string[] {
  if (doc === null) return [];
  let registry: Record<string, unknown> = {};
  for (const key of ["arms", "books", "profiles"]) {
    const v = doc[key];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      registry = v as Record<string, unknown>;
      break;
    }
  }
  const names = Object.keys(registry).filter((n) => !n.startsWith("_"));
  const retired = names.filter((n) => obj(registry[n])["enabled"] === false);
  const fridayBlock = doc["friday_entry"];
  if (fridayBlock !== undefined && obj(fridayBlock)["enabled"] === false) {
    for (const n of names) retired.push(`friday:${n}`);
  }
  return [...new Set(retired)].sort();
}

/** Each underlying's declared settlement style, under the current and the pre-SPY spelling. */
function settlementStyles(doc: Record<string, unknown> | null): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [symbol, style] of Object.entries(obj(doc?.["settlement_style"]))) {
    if (!symbol.startsWith("_") && typeof style === "string") out[symbol] = style;
  }
  const legacy = doc?.["cash_settled_symbols"];
  if (Array.isArray(legacy)) {
    for (const s of legacy) if (typeof s === "string" && !(s in out)) out[s] = "cash";
  }
  return out;
}

/** A structure tag in words. Every tag other than the two ordinary ones comes from a holiday shift
 *  (clock.py) and is named as one -- but keeps its own tag, so two holiday shapes never pool. */
function structureLabel(tag: string): string {
  if (tag === "dc_4_7") return `${tag} · normal week, Monday entry`;
  if (tag === "dc_7_10") return `${tag} · normal week, Friday entry`;
  if (tag === "dc_3_6") return `${tag} · holiday week, Tuesday entry`;
  if (tag === "dc_4_8") return `${tag} · holiday week, long expires Tuesday`;
  return `${tag} · holiday-shifted week`;
}

const NORMAL_TAGS = ["dc_4_7", "dc_7_10"];
const EXIT_ORDER = ["closed", "expired", "settled", "assigned"] as const;

interface CalendarsTrade extends SummaryTrade {
  structure: string;
  exitKind: CalendarsWeekRow["exitKind"];
  /** Debit paid, whole-position dollars, positive; null when the entry debit was not recorded. */
  debitPaid: number | null;
  symbol: string | null;
}

const rowKey = (weekOf: string, arm: string) => `${weekOf}\u0000${arm}`;

export function readCalendarsSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  // Paper only: there is no live ledger to honour `q.mode` with.
  const mode = q.mode;
  const dbFile = path.join(config.paths.calendarsDir, DB_FILE);
  if (!fs.existsSync(dbFile)) {
    const slice = emptySlice(MODULE, mode, UNIT, summaryWindow(q.range, q.day));
    slice.notes.push("No calendars ledger was found, so there is nothing to summarise yet.");
    return slice;
  }

  const doc = moduleConfig(config);
  // The history table's own rows and money: one read, unbounded, so the session arrows see every
  // week; the window is applied below with the same inclusive bounds on `week_of` the history uses.
  const all = readCalendarsWeeks(config).rows;

  // Two facts the history row does not carry, in one pass: each (week, arm)'s underlying, and the
  // weeks the entry plan was attempted. Read-only, degrading to empty on an older ledger.
  const extra = withReadOnlyDb(
    dbFile,
    { symbols: new Map<string, string>(), attempts: [] as Array<{ weekOf: string; outcome: string }> },
    (db) => {
      const symbols = new Map<string, string>();
      for (const r of db
        .prepare<[], Record<string, unknown>>(
          "SELECT week_of, arm, MIN(symbol) AS symbol FROM dc_positions GROUP BY week_of, arm",
        )
        .all()) {
        symbols.set(rowKey(str(r["week_of"]) ?? "", str(r["arm"]) ?? ""), str(r["symbol"]) ?? "");
      }
      let attempts: Array<{ weekOf: string; outcome: string }> = [];
      try {
        attempts = db
          .prepare<[], Record<string, unknown>>(
            "SELECT DISTINCT week_of, outcome FROM dc_entry_attempts",
          )
          .all()
          .map((r) => ({ weekOf: str(r["week_of"]) ?? "", outcome: str(r["outcome"]) ?? "" }));
      } catch {
        attempts = [];
      }
      return { symbols, attempts };
    },
  );

  // The history's own definition of a finished week: every position in it closed.
  const finished = (r: CalendarsWeekRow) => r.positions > 0 && r.closed === r.positions;
  const settledAll = all.filter((r) => finished(r) && r.netPnl !== null);
  const allSessions = settledAll.map((r) => r.weekOf);

  const resolvedDay = q.day === null ? sessionNeighbours(allSessions, null).day : mondayOf(q.day);
  const window = summaryWindow(q.range, resolvedDay);
  const session = sessionNeighbours(allSessions, resolvedDay);

  const trades: CalendarsTrade[] = settledAll
    .filter((r) => inWindow(r.weekOf, window))
    .map((r) => ({
      session: r.weekOf,
      arm: r.arm,
      gross: r.grossPnl ?? 0,
      fees: r.fees,
      settlement: r.settlementFees,
      slippage: r.slippage,
      net: r.netPnl ?? 0,
      structure: r.structure,
      exitKind: r.exitKind,
      debitPaid: r.entryCash === null ? null : round2(-r.entryCash),
      symbol: extra.symbols.get(rowKey(r.weekOf, r.arm)) ?? null,
    }));
  const inScope = all.filter((r) => inWindow(r.weekOf, window));
  const open = inScope.filter((r) => !finished(r)).length;

  const hero = heroOf(trades, true, open);
  const debitRecorded = trades.filter((t) => t.debitPaid !== null);
  const debitPaid =
    trades.length > 0 && debitRecorded.length === trades.length
      ? round2(trades.reduce((s, t) => s + (t.debitPaid ?? 0), 0))
      : null;
  hero.basis = debitPaid === null ? null : { label: "debit paid", amount: debitPaid };

  // ---- weeks traded vs skipped: a skipped week is one the entry plan ran for and no arm entered ----
  const tradedWeeks = new Set(inScope.map((r) => r.weekOf));
  const skippedWeeks = new Map<string, Set<string>>();
  for (const a of extra.attempts) {
    if (a.weekOf === "" || !inWindow(a.weekOf, window) || tradedWeeks.has(a.weekOf)) continue;
    const s = skippedWeeks.get(a.weekOf) ?? new Set<string>();
    s.add(a.outcome);
    skippedWeeks.set(a.weekOf, s);
  }
  const exDivSkips = [...skippedWeeks.values()].filter((s) => s.has("ex_dividend_week")).length;
  const otherSkips = skippedWeeks.size - exDivSkips;
  const otherReasons = [
    ...new Set([...skippedWeeks.values()].filter((s) => !s.has("ex_dividend_week")).flatMap((s) => [...s])),
  ].sort();
  const skipNote =
    skippedWeeks.size === 0
      ? "weeks the entry plan ran but no arm took a position"
      : `${exDivSkips} ex-dividend (skipped by design)` +
        (otherSkips > 0 ? `; ${otherSkips} other: ${otherReasons.join(", ")}` : "");

  const armsInScope = new Set(inScope.map((r) => r.arm));
  const assigned = trades.filter((t) => t.exitKind === "assigned").length;
  const holidayTrades = trades.filter((t) => !NORMAL_TAGS.includes(t.structure)).length;

  const tiles: SummaryTile[] = [
    {
      label: "weeks traded",
      value: tradedWeeks.size,
      unit: "count",
      signed: false,
      note: `${inScope.length} double calendar${inScope.length === 1 ? "" : "s"} across ${armsInScope.size} arm${armsInScope.size === 1 ? "" : "s"}`,
    },
    { label: "weeks skipped", value: skippedWeeks.size, unit: "count", signed: false, note: skipNote },
    {
      label: "average net",
      value: hero.trades > 0 ? round2(hero.net / hero.trades) : null,
      unit: "usd",
      note: `per finished double calendar (${hero.trades})`,
    },
    {
      label: "average debit paid",
      value: debitPaid === null ? null : round2(debitPaid / trades.length),
      unit: "usd",
      signed: false,
      note:
        debitPaid === null && trades.length > 0
          ? `n/r: ${debitRecorded.length}/${trades.length} rows recorded their debit`
          : "per double calendar, both calendars together",
    },
    {
      label: "ended with shares delivered",
      value: hero.trades > 0 ? assigned : null,
      unit: "count",
      signed: false,
      note: `of ${hero.trades} finished; SPY settles by delivery, shares sold the next session`,
    },
    {
      label: "holiday-week trades",
      value: hero.trades > 0 ? holidayTrades : null,
      unit: "count",
      signed: false,
      note: `of ${hero.trades}; a different trade from a normal week, never pooled`,
    },
  ];

  const gauges: SummaryGauge[] = [
    {
      title: "return on debit paid",
      rawPct: debitPaid === null || debitPaid === 0 ? null : (hero.net / debitPaid) * 100,
      min: -100,
      max: 100,
      caption:
        debitPaid !== null
          ? `net ÷ debit paid over ${trades.length} finished double calendar${trades.length === 1 ? "" : "s"}`
          : trades.length === 0
            ? "no finished double calendars in this window"
            : `n/r: ${debitRecorded.length}/${trades.length} rows recorded their debit`,
    },
  ];

  const styles = settlementStyles(doc);
  const structureOrder = [
    ...NORMAL_TAGS,
    ...[...new Set(trades.map((t) => t.structure))].filter((s) => !NORMAL_TAGS.includes(s)).sort(),
  ].map(structureLabel);
  const breakdowns = [
    breakdownOf("by week type", "structure", trades, (t) => structureLabel(t.structure), structureOrder),
    breakdownOf("by how it ended", "ended", trades, (t) => t.exitKind, EXIT_ORDER),
    breakdownOf("by underlying", "underlying (settlement)", trades, (t) =>
      t.symbol === null || t.symbol === "" ? null : `${t.symbol} (${styles[t.symbol] ?? "undeclared"})`,
    ),
  ];

  const notes = [
    "Paper only. A trade is one arm's double calendar for one week: a put calendar below the market and a call calendar above it, bought together for a debit. A week is dated by its Monday, so each step of the day arrows is one week.",
    "Fills are modelled at the mid price; fees, settlement charges and slippage are each charged as costs and subtracted once from gross to give net. Gross includes any shares delivered at Friday's expiry and sold the next session.",
    "control sells everything at Friday's close; path holds to expiry to record the full price path; friday: arms enter the same contracts on the Friday before. Every arm shares one entry plan, so a difference between control and path is the exit and nothing else.",
    "Holiday weeks are different trades (a shorter or longer calendar) and keep their own rows, never pooled with normal weeks. Weeks with an SPY ex-dividend date are skipped on purpose, so these results describe ordinary weeks only.",
    "Early assignment is not modelled, so the results are an upper bound. For path, the debit paid understates the risk: shares delivered on Friday carry the weekend's move.",
  ];
  if (hero.open > 0) {
    notes.push(
      `${hero.open} double calendar${hero.open === 1 ? " is" : "s are"} still open in this window; none of their money is in the figures above.`,
    );
  }

  return {
    module: MODULE,
    mode,
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns,
    arms: armsOf(trades, retiredArms(doc)),
    notes,
  };
}
