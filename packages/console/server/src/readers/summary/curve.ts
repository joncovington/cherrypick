import path from "node:path";
import type { ModuleSummarySlice, SummaryGauge, SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { hasTable, num, str, withReadSnapshot, type DatabaseHandle } from "../db.js";
import { positionCash, positionCashColumns } from "../positionCash.js";
import {
  WEEKDAYS,
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  worstNet,
  inWindow,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  weekdayOf,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * curve's simplified-view summary: VXX call credit spreads, paper only (no live ledger exists, so
 * `q.mode` is never consulted and the slice always says paper).
 *
 * Money is `readCurveHistory`'s own: the same `curve_positions` rows (`status = 'closed'`, booked to
 * `closed_session`, the session a result was realised on), the same `positionCashColumns` SELECT and
 * the same `positionCash` arithmetic per row. Slippage is charged as a cost here (fills at mid), so
 * it is its own hero line.
 *
 * KPIs are the module's own vocabulary (packages/curve/CLAUDE.md): return on max loss (its replay's
 * yardstick and, since 2026-10-06, its credit floor's denominator), credit as a share of width (the
 * worksheet's stored measure), cost drag (costs are what eat this edge), the regime series'
 * continuity (written every session, traded or not), and early-assignment exposure (measured, never
 * modelled -- the paper net is an upper bound).
 */

const UNIT = "a VXX call credit spread";
const DB_FILE = "paper_trades.db";

interface CurveTrade extends SummaryTrade {
  entrySession: string;
  entryRegime: string | null;
  exitReason: string | null;
  exitKind: string | null;
  /** Whole-position dollars: entry_max_loss (per share) x 100 x quantity; null where unrecorded. */
  maxLoss: number | null;
  premium: number | null;
  creditPctOfWidth: number | null;
  exposed: boolean;
}

interface LedgerRead {
  trades: CurveTrade[];
  unrecordedMoney: number;
  open: number;
  regime: { written: number; usable: number; contango: number; backwardation: number };
  enteredSessions: number;
  breaks: Array<{ date: string; key: string }>;
}

type Window = { from: string | null; to: string | null };

const round2 = (v: number) => Math.round(v * 100) / 100;
const pctOf = (part: number, whole: number): number | null => (whole > 0 ? (part / whole) * 100 : null);

/** Every session a closed position is booked to (readCurveHistory's date: the close). */
function settledSessions(db: DatabaseHandle): string[] {
  return db
    .prepare<[], { s: string }>(
      "SELECT DISTINCT closed_session AS s FROM curve_positions WHERE status = 'closed' AND closed_session IS NOT NULL",
    )
    .all()
    .map((r) => r.s);
}

function readLedger(db: DatabaseHandle, w: Window): LedgerRead {
  const exposedIds = new Set(
    hasTable(db, "curve_marks")
      ? db
          .prepare<[], { position_id: string }>(
            // analytics.exposure()'s population: usable marks with a measured short extrinsic.
            "SELECT DISTINCT position_id FROM curve_marks WHERE assignment_exposed = 1 AND short_tv IS NOT NULL AND usable = 1",
          )
          .all()
          .map((r) => r.position_id)
      : [],
  );
  // readCurveHistory's WHERE and money columns, plus the entry measures the KPIs read.
  const where = ["status = 'closed'"];
  const params: string[] = [];
  if (w.from !== null) {
    where.push("substr(closed_session, 1, 10) >= ?");
    params.push(w.from);
  }
  if (w.to !== null) {
    where.push("substr(closed_session, 1, 10) <= ?");
    params.push(w.to);
  }
  const rows = db
    .prepare<string[], Record<string, unknown>>(
      `SELECT position_id, arm, entry_session, closed_session, exit_reason, entry_credit, entry_regime,
              entry_max_loss, entry_credit_pct_of_width, ${positionCashColumns(db, "curve_")}
         FROM curve_positions WHERE ${where.join(" AND ")}`,
    )
    .all(...params);

  const trades: CurveTrade[] = [];
  let unrecordedMoney = 0;
  for (const r of rows) {
    const money = positionCash(r, num(r["entry_credit"]));
    const session = str(r["closed_session"]);
    if (session === null || money.netPnl === null || money.grossPnl === null) {
      // The history table sums such a row as zero; it carries no money here either.
      unrecordedMoney += 1;
      continue;
    }
    const qty = num(r["quantity"]) ?? 1;
    const maxLoss = num(r["entry_max_loss"]);
    const credit = num(r["entry_credit"]);
    trades.push({
      session,
      arm: str(r["arm"]) ?? "",
      gross: money.grossPnl,
      fees: money.fees,
      settlement: money.settlementFees,
      slippage: money.slippage,
      net: money.netPnl,
      entrySession: str(r["entry_session"]) ?? session,
      entryRegime: str(r["entry_regime"]),
      exitReason: str(r["exit_reason"]),
      exitKind: money.exitKind,
      maxLoss: maxLoss === null ? null : round2(maxLoss * 100 * qty),
      premium: credit === null ? null : round2(credit * 100 * qty),
      creditPctOfWidth: num(r["entry_credit_pct_of_width"]),
      exposed: exposedIds.has(str(r["position_id"]) ?? ""),
    });
  }

  // Positions still open that had been entered by the window's end; none of their money is above.
  const open =
    db
      .prepare<[string | null, string | null], { n: number }>(
        "SELECT COUNT(*) AS n FROM curve_positions WHERE status != 'closed' AND (? IS NULL OR entry_session <= ?)",
      )
      .get(w.to, w.to)?.n ?? 0;

  const regime = { written: 0, usable: 0, contango: 0, backwardation: 0 };
  if (hasTable(db, "curve_regime")) {
    for (const r of db.prepare<[], Record<string, unknown>>("SELECT trade_date, regime, usable FROM curve_regime").all()) {
      if (!inWindow(str(r["trade_date"]) ?? "", w)) continue;
      regime.written += 1;
      if (r["usable"] !== 1) continue;
      regime.usable += 1;
      if (r["regime"] === "contango") regime.contango += 1;
      else if (r["regime"] === "backwardation") regime.backwardation += 1;
    }
  }
  const enteredSessions = db
    .prepare<[], { s: string }>("SELECT DISTINCT entry_session AS s FROM curve_positions")
    .all()
    .filter((r) => inWindow(r.s, w)).length;

  const breaks = hasTable(db, "measurement_breaks")
    ? db
        .prepare<[], Record<string, unknown>>("SELECT break_date, key FROM measurement_breaks ORDER BY break_date")
        .all()
        .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "" }))
    : [];

  return { trades, unrecordedMoney, open, regime, enteredSessions, breaks };
}

export function readCurveSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const file = path.join(config.paths.curveDir, DB_FILE);
  const missing = (): ModuleSummarySlice => {
    const s = emptySlice("curve", "paper", UNIT, summaryWindow(q.range, q.day));
    s.notes.push("No curve ledger was found (or it could not be read), so there is nothing to summarise yet.");
    return s;
  };

  const sessions = withReadSnapshot<string[] | null>(file, null, (db) => settledSessions(db));
  if (sessions === null) return missing();
  const session = sessionNeighbours(sessions, q.day);
  const window = summaryWindow(q.range, session.day);
  const read = withReadSnapshot<LedgerRead | null>(file, null, (db) => readLedger(db, window));
  if (read === null) return missing();

  const trades = scopeTrades(read.trades, q);
  const hero = heroOf(trades, true, read.open);
  const premiumRows = trades.filter((t) => t.premium !== null);
  const premium = round2(premiumRows.reduce((s, t) => s + (t.premium ?? 0), 0));
  hero.basis = trades.length > 0 ? { label: "premium collected", amount: premium } : null;

  // Return on max loss: the window's net over the max loss its trades put at risk.
  const withMaxLoss = trades.filter((t) => t.maxLoss !== null && t.maxLoss > 0);
  const atRisk = withMaxLoss.reduce((s, t) => s + (t.maxLoss ?? 0), 0);
  const gauges: SummaryGauge[] = [
    {
      title: "return on max loss",
      rawPct: pctOf(withMaxLoss.reduce((s, t) => s + t.net, 0), atRisk),
      min: -100,
      max: 100,
      caption:
        withMaxLoss.length === 0
          ? "n/r: no settled trade recorded its max loss"
          : `net ÷ max loss at entry (width − credit, whole position), ${withMaxLoss.length}/${trades.length} trades`,
      dollars:
        withMaxLoss.length === 0
          ? null
          : {
              amount: Math.round(withMaxLoss.reduce((s, t) => s + t.net, 0) * 100) / 100,
              of: Math.round(atRisk * 100) / 100,
              ofLabel: "max loss at entry",
              worst: worstNet(withMaxLoss),
            },
    },
  ];

  const feesLine = hero.costs.find((c) => c.label === "fees")?.amount ?? null;
  // Every cost subtracted from gross, whichever line holds it: gross − net by the rows' identity.
  const allCosts = round2(hero.gross - hero.net);
  const fullPremium = trades.length > 0 && premiumRows.length === trades.length && premium > 0;
  const creditPct = trades.filter((t) => t.creditPctOfWidth !== null);
  const exposed = trades.filter((t) => t.exposed).length;
  const delivered = trades.filter((t) => t.exitKind === "assigned").length;

  const tiles: SummaryTile[] = [
    {
      label: "fee drag",
      value: feesLine === null || !fullPremium ? null : (feesLine / premium) * 100,
      unit: "pct",
      note: "trading fees ÷ premium collected",
      signed: false,
    },
    {
      label: "cost drag",
      value: fullPremium ? (allCosts / premium) * 100 : null,
      unit: "pct",
      note: "fees + slippage + settlement ÷ premium collected",
      signed: false,
    },
    {
      label: "credit as % of width",
      value:
        creditPct.length > 0
          ? (creditPct.reduce((s, t) => s + (t.creditPctOfWidth ?? 0), 0) / creditPct.length) * 100
          : null,
      unit: "pct",
      note: `average entry credit ÷ spread width, ${creditPct.length}/${trades.length} trades`,
      signed: false,
    },
    {
      label: "assignment-exposed trades",
      value: exposed,
      unit: "count",
      note: `of ${trades.length} settled: the short's time value fell under the threshold at some mark`,
      signed: false,
    },
    {
      label: "ended with shares delivered",
      value: delivered,
      unit: "count",
      note: `of ${trades.length} settled: a leg finished in the money at expiry`,
      signed: false,
    },
    {
      label: "regime days recorded",
      value: read.regime.usable,
      unit: "days",
      note: `usable of ${read.regime.written} written; ${read.regime.contango} contango, ${read.regime.backwardation} backwardation`,
      signed: false,
    },
    {
      label: "sessions with an entry",
      value: read.enteredSessions,
      unit: "days",
      note: `any arm, against ${read.regime.contango} contango days recorded`,
      signed: false,
    },
  ];

  const notes: string[] = [
    "A trade here is one VXX call credit spread: sell a call, buy a higher call as a wing, same monthly expiry. Paper only: fills at mid, with fees and slippage charged as costs.",
    "Gross includes any shares delivered at expiry and their sale the next session; net is gross less every cost.",
    "Early assignment is not modelled, only measured (the assignment-exposed count), and neither are VXX reverse splits, so the paper net is an upper bound, not a forecast.",
    "Arms enter only on a contango day (VIX below VIX3M by a buffer). The regime is recorded every session whether or not anything trades, so recorded days outnumber entry days by design.",
    "control and noflip are identical until a regime flip actually fires; the gap between them only means something once flips have happened.",
  ];
  if (read.unrecordedMoney > 0) {
    notes.push(
      `${read.unrecordedMoney} closed trade(s) in this window never recorded their gross or fees and are left out of the money.`,
    );
  }
  const straddled = read.breaks.filter(
    (b) => trades.some((t) => t.entrySession < b.date) && trades.some((t) => t.entrySession >= b.date),
  );
  const lastBreak = straddled[straddled.length - 1];
  if (lastBreak !== undefined) {
    notes.push(
      `This window mixes trades entered before and after the ${lastBreak.date} change (${lastBreak.key}); the module never pools across it, so read the totals with care.`,
    );
  }

  return {
    module: "curve",
    mode: "paper",
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns: [
      breakdownOf("by regime at entry", "regime", trades, (t) => t.entryRegime ?? "unrecorded"),
      breakdownOf("by exit reason", "exit", trades, (t) => t.exitReason ?? "unrecorded"),
      breakdownOf("by entry weekday", "weekday", trades, (t) => weekdayOf(t.entrySession), WEEKDAYS),
    ],
    arms: armsOf(trades, readRetiredArms(config).retired.curve),
    notes,
  };
}
