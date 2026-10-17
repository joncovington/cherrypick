import path from "node:path";
import type { ContangoHistoryRow, ModuleSummarySlice, SummaryGauge, SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { readContangoHistory } from "../contango.js";
import { NO_RANGE } from "../dateRange.js";
import { hasTable, str, withReadSnapshot } from "../db.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  inWindow,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * Contango's simplified-view summary. A trade here is one holding stint: the SVXY or SHV shares an
 * arm bought on one switch and sold on the next. The money is `readContangoHistory`'s own rows
 * (entry + exit + distributions = gross; gross − fees − slippage = net), booked to the session the
 * stint was sold, as that history books it. Paper only: the module has no live ledger, so `q.mode`
 * is not read.
 *
 * The module's owner judges it by each arm's daily NAV, not stint P&L, and that tear sheet lives on
 * the module's own page. What this slide adds is the shape of the window: return on the cash the
 * stints deployed, how the arms' sessions split between the risk fund and cash, how often they
 * switched or missed the decision window, the distributions received, and the slippage paid
 * against the 2 bps a side the replay assumed.
 */

const UNIT = "one SVXY or SHV holding, switch to switch";
const DB_FILE = "paper_trades.db";

const round2 = (v: number) => Math.round(v * 100) / 100;

interface StintTrade extends SummaryTrade {
  symbol: string;
  exitReason: string | null;
  /** The purchase, as a positive amount; null when the entry value was not recorded. */
  deployed: number | null;
  days: number | null;
  distributions: number | null;
  /** Purchase + sale, both positive: the denominator for slippage as a share of traded value. */
  traded: number | null;
}

/** Calendar days from purchase to sale. */
function daysHeld(entry: string, exit: string): number | null {
  const a = Date.parse(`${entry.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${exit.slice(0, 10)}T00:00:00Z`);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 86_400_000) : null;
}

const LENGTH_ORDER = ["1 day or less", "2–5 days", "6–20 days", "21+ days"] as const;

function lengthBucket(days: number | null): string | null {
  if (days === null) return null;
  if (days <= 1) return LENGTH_ORDER[0];
  if (days <= 5) return LENGTH_ORDER[1];
  if (days <= 20) return LENGTH_ORDER[2];
  return LENGTH_ORDER[3];
}

function reasonLabel(reason: string | null): string {
  if (reason === null || reason === "") return "not recorded";
  const m = /^switch_to_(.+)$/.exec(reason);
  return m ? `switched to ${m[1]}` : reason.replace(/_/g, " ");
}

function toTrade(r: ContangoHistoryRow): StintTrade {
  const traded = r.entryCash === null || r.exitCash === null ? null : Math.abs(r.entryCash) + Math.abs(r.exitCash);
  return {
    session: (r.exitSession ?? "").slice(0, 10),
    arm: r.arm,
    gross: r.grossPnl ?? 0,
    fees: r.fees,
    // A share trade has no settlement: the history reader states it null, and so does this.
    settlement: r.settlementFees,
    slippage: r.slippage,
    net: r.netPnl ?? 0,
    symbol: r.symbol,
    exitReason: r.exitReason,
    deployed: r.entryCash === null ? null : Math.abs(r.entryCash),
    days: r.exitSession === null ? null : daysHeld(r.entrySession, r.exitSession),
    distributions: r.distributions,
    traded,
  };
}

interface LedgerFacts {
  /** The session each open stint was bought. */
  openEntries: string[];
  /** Each arm-session's NAV-log row, compact: for sessions held, switches and missed windows. */
  sessions: Array<{ date: string; action: string | null; state: string | null; symbol: string | null }>;
}

export function readContangoSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const dbFile = path.join(config.paths.contangoDir, DB_FILE);
  const facts = withReadSnapshot<LedgerFacts | null>(dbFile, null, (db) => {
    if (!hasTable(db, "contango_positions")) return null;
    const openEntries = db
      .prepare<[], { s: string | null }>("SELECT entry_session AS s FROM contango_positions WHERE status = 'open'")
      .all()
      .map((r) => (r.s ?? "").slice(0, 10));
    const sessions = hasTable(db, "contango_sessions")
      ? db
          .prepare<[], Record<string, unknown>>("SELECT trade_date, action, state_after, holding_symbol FROM contango_sessions")
          .all()
          .map((r) => ({
            date: (str(r["trade_date"]) ?? "").slice(0, 10),
            action: str(r["action"]),
            state: str(r["state_after"]),
            symbol: str(r["holding_symbol"]),
          }))
      : [];
    return { openEntries, sessions };
  });

  if (facts === null) {
    const slice = emptySlice("contango", "paper", UNIT, summaryWindow(q.range, q.day));
    slice.notes.push("No contango ledger was found, so there is nothing to summarise yet.");
    return slice;
  }

  // Every closed stint through the history reader's own arithmetic; the window is applied below.
  const all = readContangoHistory(config, { arm: null, range: NO_RANGE }).rows.filter((r) => r.exitSession !== null);
  const session = sessionNeighbours(
    all.map((r) => (r.exitSession ?? "").slice(0, 10)),
    q.day,
  );
  const window = summaryWindow(q.range, session.day);
  const trades = scopeTrades(all.map(toTrade).filter((t) => inWindow(t.session, window)), q);

  const open = facts.openEntries.filter((s) => window.to === null || s <= window.to).length;
  const hero = heroOf(trades, true, open);
  const settlement = hero.costs.find((c) => c.label === "settlement");
  if (settlement !== undefined) settlement.note = "n/r: a share trade has no settlement";

  const deployedRows = trades.filter((t) => t.deployed !== null);
  const deployed = round2(deployedRows.reduce((s, t) => s + (t.deployed ?? 0), 0));
  hero.basis = trades.length > 0 ? { label: "cash deployed", amount: deployed } : null;
  const deployedComplete = deployedRows.length === trades.length;

  // --- tiles -------------------------------------------------------------------------------
  const tiles: SummaryTile[] = [];
  const winSessions = facts.sessions.filter((s) => inWindow(s.date, window));
  // Sessions held per fund, the risk fund first.
  const held = new Map<string, number>();
  for (const s of winSessions) {
    if (s.state === null || s.symbol === null) continue;
    const key = `${s.state === "risk" ? 0 : 1}|${s.symbol}`;
    held.set(key, (held.get(key) ?? 0) + 1);
  }
  for (const [key, n] of [...held.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    tiles.push({
      label: `sessions in ${key.split("|")[1]}`,
      value: n,
      unit: "count",
      note: "arm-sessions in the window, every arm counted, from the daily NAV log",
      signed: false,
    });
  }
  tiles.push({
    label: "switches",
    value: winSessions.filter((s) => s.action === "switch").length,
    unit: "count",
    note: "arm-sessions that sold one fund and bought the other",
    signed: false,
  });
  tiles.push({
    label: "missed windows",
    value: winSessions.filter((s) => s.action === "missed").length,
    unit: "count",
    note: "no decision inside the window; the arm kept what it held",
    signed: false,
  });
  tiles.push({
    label: "distributions received",
    value: trades.length === 0 ? null : round2(trades.reduce((s, t) => s + (t.distributions ?? 0), 0)),
    unit: "usd",
    note: `already inside gross; on the ${trades.length} stints sold in the window`,
    signed: false,
  });
  const slipRows = trades.filter((t) => t.slippage !== null && t.traded !== null && t.traded > 0);
  const traded = slipRows.reduce((s, t) => s + (t.traded ?? 0), 0);
  tiles.push({
    label: "slippage, % of traded value",
    value: traded > 0 ? Math.round((slipRows.reduce((s, t) => s + (t.slippage ?? 0), 0) / traded) * 1e6) / 1e4 : null,
    unit: "pct",
    note: `slippage ÷ (purchase + sale), ${slipRows.length}/${trades.length} stints; the replay assumes 0.02% a side`,
    signed: false,
  });
  const dayRows = trades.filter((t) => t.days !== null);
  tiles.push({
    label: "average stint",
    value: dayRows.length > 0 ? round2(dayRows.reduce((s, t) => s + (t.days ?? 0), 0) / dayRows.length) : null,
    unit: "days",
    note: `calendar days from purchase to sale, ${dayRows.length} stints`,
    signed: false,
  });

  // --- gauge ------------------------------------------------------------------------------
  const gauges: SummaryGauge[] =
    trades.length === 0
      ? []
      : [
          {
            title: "return on cash deployed",
            rawPct: deployedComplete && deployed > 0 ? (hero.net / deployed) * 100 : null,
            min: -10,
            max: 10,
            caption: deployedComplete
              ? `net ÷ cash deployed (each stint's purchase), ${trades.length} stints`
              : `n/r: ${trades.length - deployedRows.length}/${trades.length} stints recorded no purchase`,
          },
        ];

  // --- breakdowns -------------------------------------------------------------------------
  const breakdowns = [
    breakdownOf("by holding", "fund", trades, (t) => t.symbol || null),
    breakdownOf("by how it ended", "exit", trades, (t) => reasonLabel(t.exitReason)),
    breakdownOf("by stint length", "calendar days held", trades, (t) => lengthBucket(t.days), LENGTH_ORDER),
  ];

  return {
    module: "contango",
    mode: "paper",
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns,
    arms: armsOf(trades, readRetiredArms(config).retired.contango ?? []),
    notes: [
      "A trade here is one holding stint: the shares an arm bought on one switch and sold on the next. " +
        "Every arm always holds either SVXY or SHV, so each arm has a stint open at any time, and that stint's money is not in these figures.",
      "A stint is booked to the session it was sold. Gross includes the fund's distributions (SHV's monthly dividend is most of its return).",
      "Paper fills at mid; slippage (half the quoted spread, at least 2 bps of mid) is charged as its own cost. Fees are the sale's SEC and TAF fees; buys are free. A share trade has no settlement.",
      "The module is judged on each arm's daily NAV, not on stint P&L: a stint's result cannot show a drawdown inside it. The contango page's arms and performance tabs carry that.",
      "Paper only: this module has no live path.",
    ],
  };
}
