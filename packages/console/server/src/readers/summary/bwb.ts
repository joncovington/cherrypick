import path from "node:path";
import type { BwbCycleRow, ModuleSummarySlice, SummaryGauge, SummaryTile, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { readBwbHistory } from "../bwb.js";
import { hasColumn, num, readOnlyDb, str } from "../db.js";
import { MAX_PAGE } from "../paging.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  worstNet,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  weekdayOf,
  WEEKDAYS,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * bwb's simplified-view summary.
 *
 * Money is `readBwbHistory`'s own rows, read page by page over the window -- not a re-derivation of
 * them -- so the hero is the history table's totals for the same window by construction: same
 * predicate (`status = 'closed'`, bounded on the close session), same per-row arithmetic
 * (`bwbTradeCash`). One extra pass over the same rows fetches what the history row does not carry
 * (entry DTE, the fly's widths), and the open count.
 *
 * Paper and live are separate files (`paper_trades.db`, `live_trades.db`), never merged; `q.mode`
 * picks one. On a live row the slippage is MEASURED against mid and never charged into fees (the
 * live loop's own rule, mirrored in `bwbTradeCash`), so the live hero carries no slippage line.
 */

const UNIT = "a ~7 DTE SPX put broken-wing butterfly, held to expiry";
const MODULE = "bwb";

/** The ledger file for a mode -- the same two names `readers/bwb.ts` opens. */
function ledgerPath(config: ConsoleConfig, mode: TradingMode): string {
  return path.join(config.paths.bwbDir, mode === "live" ? "live_trades.db" : "paper_trades.db");
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** What the history row lacks, from the same rows. */
interface Extra {
  entryDte: number | null;
  /** The fly's own worst case per share, `engine.bwb_max_losses`: max(0, wide - narrow - credit). */
  maxLossPerShare: number | null;
  quantity: number;
}

interface BwbSummaryTrade extends SummaryTrade {
  row: BwbCycleRow;
  extra: Extra | undefined;
}

const EXIT_ORDER = ["expired worthless", "settled ITM", "closed early"] as const;
const exitLabel = (k: BwbCycleRow["exitKind"]): string | null =>
  k === "expired" ? "expired worthless" : k === "settled" ? "settled ITM" : k === "closed" ? "closed early" : null;

const DTE_ORDER = Array.from({ length: 8 }, (_, i) => `${i} DTE`);

export function readBwbSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const mode = q.mode;
  const file = ledgerPath(config, mode);

  const pass = readOnlyDb(file, (db) => {
    // The session a result is booked to is its close (`readBwbHistory`'s range bounds closed_session).
    const sessions = db
      .prepare<[], { s: string | null }>(
        `SELECT DISTINCT substr(closed_session, 1, 10) AS s FROM bwb_positions
          WHERE status = 'closed' AND closed_session IS NOT NULL ORDER BY s`,
      )
      .all()
      .map((r) => r.s)
      .filter((s): s is string => s !== null);
    const resolvedDay = q.day ?? sessions[sessions.length - 1] ?? null;
    const win = summaryWindow(q.range, resolvedDay);

    // The same predicate as the history read: closed, the close session inside the window.
    const clauses = ["status = 'closed'"];
    const params: string[] = [];
    if (win.from !== null) {
      clauses.push("substr(closed_session, 1, 10) >= ?");
      params.push(win.from);
    }
    if (win.to !== null) {
      clauses.push("substr(closed_session, 1, 10) <= ?");
      params.push(win.to);
    }
    const col = (name: string) => (hasColumn(db, "bwb_positions", name) ? name : "NULL");
    const extras = new Map<string, Extra>();
    for (const r of db
      .prepare<string[], Record<string, unknown>>(
        `SELECT position_id, ${col("entry_dte")} AS entry_dte, ${col("entry_narrow_width")} AS narrow,
                ${col("entry_wide_width")} AS wide, entry_credit, quantity
           FROM bwb_positions WHERE ${clauses.join(" AND ")}`,
      )
      .all(...params)) {
      const narrow = num(r["narrow"]);
      const wide = num(r["wide"]);
      const credit = num(r["entry_credit"]);
      extras.set(str(r["position_id"]) ?? "", {
        entryDte: num(r["entry_dte"]),
        maxLossPerShare: narrow === null || wide === null || credit === null ? null : Math.max(0, wide - narrow - credit),
        quantity: num(r["quantity"]) ?? 1,
      });
    }

    // Open = what the module's open-positions card lists (not closed, not cancelled), entered in
    // the window. None of its money is in the hero.
    const openWhere = ["status NOT IN ('closed', 'cancelled')"];
    const openParams: string[] = [];
    if (win.from !== null) {
      openWhere.push("entry_session >= ?");
      openParams.push(win.from);
    }
    if (win.to !== null) {
      openWhere.push("entry_session <= ?");
      openParams.push(win.to);
    }
    const open = Number(
      db
        .prepare<string[], { n: number }>(`SELECT COUNT(*) AS n FROM bwb_positions WHERE ${openWhere.join(" AND ")}`)
        .get(...openParams)?.n ?? 0,
    );
    return { sessions, resolvedDay, win, extras, open };
  });

  if (pass.status !== "ok") {
    const slice = emptySlice(MODULE, mode, UNIT, summaryWindow(q.range, q.day));
    slice.notes.push(
      pass.status === "absent"
        ? `No ${mode} bwb ledger on this machine yet${mode === "live" ? " -- the live arm has not traded" : ""}.`
        : `The ${mode} bwb ledger could not be read (${pass.error}).`,
    );
    return slice;
  }
  const { sessions, resolvedDay, win, extras, open } = pass.value;

  // The history reader's own rows for the window, every page of them.
  const rows: BwbCycleRow[] = [];
  for (let offset = 0; ; offset += MAX_PAGE) {
    const page = readBwbHistory(
      config,
      { arm: null, symbol: null, range: { from: win.from, to: win.to } },
      { limit: MAX_PAGE, offset },
      mode,
    );
    rows.push(...page.rows);
    if (page.rows.length < MAX_PAGE || rows.length >= page.total) break;
  }

  const trades: BwbSummaryTrade[] = scopeTrades(rows.map((r): BwbSummaryTrade => ({
    session: (r.closedSession ?? r.entrySession).slice(0, 10),
    arm: r.arm,
    gross: r.grossPnl ?? 0,
    fees: r.fees,
    settlement: r.settlementFees,
    slippage: r.slippage,
    net: r.netPnl ?? 0,
    row: r,
    extra: extras.get(r.positionId),
  })), q);

  const live = mode === "live";
  const hero = heroOf(trades, !live, open, (t) =>
    t.extra?.maxLossPerShare == null ? null : t.extra.maxLossPerShare * 100 * t.extra.quantity,
  );
  const premium = round2(trades.reduce((s, t) => s + (t.row.entryCash ?? 0) + (t.row.addOnCash ?? 0), 0));
  hero.basis = trades.length > 0 ? { label: "premium collected (fly + add-on credits)", amount: premium } : null;
  const n = trades.length;

  // -- Return on max loss: net over the fly's worst case at entry, whole position. The worst case
  //    is `engine.bwb_max_losses` on the recorded widths and credit -- the recorded
  //    `entry_max_loss` is overstated by the narrow width on every row before 2026-09-18 (the
  //    module's CLAUDE.md, honesty rule 10), and this is the derivation that doc names.
  const capRows = trades.filter((t) => t.extra?.maxLossPerShare != null);
  const capital = round2(capRows.reduce((s, t) => s + (t.extra!.maxLossPerShare ?? 0) * 100 * t.extra!.quantity, 0));
  const netRaw = trades.reduce((s, t) => s + t.net, 0);
  const rorRaw = capRows.length > 0 && capital > 0 ? (netRaw / capital) * 100 : null;
  const gauges: SummaryGauge[] = [
    {
      title: "return on max loss",
      rawPct: rorRaw,
      min: -100,
      max: 100,
      caption:
        rorRaw === null
          ? "n/r: no settled position in the window recorded its widths and credit"
          : `net ÷ $${capital.toLocaleString("en-US")} of the fly's worst case at entry (${capRows.length}/${n} positions); a fired add-on's extra risk is not in it`,
      dollars:
        rorRaw === null ? null : { amount: round2(netRaw), of: capital, ofLabel: "worst case at entry", worst: worstNet(trades) },
    },
  ];

  // -- Tiles, in the module's own vocabulary.
  const credits = trades.map((t) => t.row.entryCredit).filter((c): c is number => c !== null);
  const fired = trades.filter((t) => t.row.addonFiredAt !== null);
  const fireSessions = new Set(fired.map((t) => (t.row.addonFiredAt ?? "").slice(0, 10)));
  const addOnCredit = round2(fired.reduce((s, t) => s + (t.row.addOnCash ?? 0), 0));
  const itm = trades.filter((t) => t.row.exitKind === "settled").length;
  const feesLine = hero.costs.find((c) => c.label === "fees")?.amount ?? null;
  const tiles: SummaryTile[] = [
    {
      label: "avg entry credit",
      value: credits.length > 0 ? round2(credits.reduce((s, c) => s + c, 0) / credits.length) : null,
      unit: "price",
      note: `per share, the fly alone, ${credits.length}/${n} positions`,
      signed: false,
    },
    {
      label: "add-on fired",
      value: fired.length,
      unit: "count",
      note: `of ${n} positions, on ${fireSessions.size} distinct session${fireSessions.size === 1 ? "" : "s"}; control never fires`,
      signed: false,
    },
    {
      label: "add-on credit",
      value: n > 0 ? addOnCredit : null,
      unit: "usd",
      note: "opening credit of the fired add-ons; their own P&L is not split out in the ledger",
    },
    {
      label: "settled ITM",
      value: itm,
      unit: "count",
      note: `of ${n} positions held to expiry with a leg in the money`,
      signed: false,
    },
    {
      label: "fee drag",
      value: feesLine !== null && premium > 0 ? round2((feesLine / premium) * 100) : null,
      unit: "pct",
      note: "trading fees ÷ premium collected (fly + add-on credits)",
      signed: false,
    },
  ];

  const breakdowns = [
    breakdownOf("by how it ended", "exit", trades, (t) => exitLabel(t.row.exitKind), EXIT_ORDER),
    breakdownOf("by entry weekday", "entry day", trades, (t) => weekdayOf(t.row.entrySession), WEEKDAYS),
    breakdownOf(
      "by days to expiry at entry",
      "entry DTE",
      trades,
      (t) => (t.extra?.entryDte == null ? null : `${t.extra.entryDte} DTE`),
      DTE_ORDER,
    ),
  ];

  const notes = [
    "A trade is one put broken-wing butterfly on SPX, entered every session and held to its Friday expiry. Every arm enters the identical fly; they differ only in whether and when a put credit spread add-on fires.",
    `Gross is the fly and any add-on through settlement. Net takes off trading fees and the $5 cash-settlement fee per in-the-money strike${live ? "" : ", plus modelled slippage (paper fills at mid)"}, each once.`,
    "Positions are booked to the session they closed on, not the one they opened on.",
    "Overlapping positions share the same market: one selloff can fire the add-on across several at once, so fired positions are not independent samples. The distinct sessions are the closer count.",
  ];
  if (live) {
    notes.push("Live fills are the broker's. Slippage is measured against mid but never charged as a cost, so there is no slippage line.");
  } else {
    notes.push("Paper settles at intrinsic against the last cached tick, not the official print.");
  }

  return {
    module: MODULE,
    mode,
    window: win,
    session: sessionNeighbours(sessions, resolvedDay),
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns,
    arms: armsOf(trades, readRetiredArms(config).retired.bwb ?? []),
    notes,
  };
}
