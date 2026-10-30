/**
 * MEIC live against paper (docs/console-review-plan.md batch 5): each live condor beside the paper
 * arm that trades what live trades -- `live-shadow`, a `follows_live` arm merged onto the `live`
 * block, so it is live's twin by construction -- paired by session and, within a session, by entry
 * order. Paper is read from the first live session on.
 *
 * A paper session with no live row is kept and counted apart: live was not armed that day, or
 * refused. What live sat out is part of the gap between the two, not noise to drop. Money follows
 * the MEIC page: whole-position dollars, net = gross − fees (settlement inside fees), open rows and
 * rows without recorded fees carry no net. The paired totals are the sums of their rows.
 */

import path from "node:path";
import type { MeicLiveGapPayload, MeicLiveGapRow, MeicLiveGapSide } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { findArmColumn, hasColumn, num, readOnlyDb, str, type DatabaseHandle } from "./db.js";

export const LIVE_ARM = "live";
export const TWIN_ARM = "live-shadow";

const RESOLVED = "status NOT IN ('cancelled','pending','partial_entry')";

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function col(db: DatabaseHandle, name: string): string {
  return hasColumn(db, "ic_trades", name) ? name : "NULL";
}

function side(r: Record<string, unknown>): MeicLiveGapSide {
  const gross = num(r["pnl"]);
  const fees = num(r["fees"]);
  return {
    entry: str(r["entry_time"]),
    symbol: str(r["symbol"]),
    putStrike: num(r["put_strike"]),
    callStrike: num(r["call_strike"]),
    width: num(r["wing_width"]),
    credit: num(r["net_credit"]),
    status: str(r["status"]),
    gross,
    fees,
    net: gross !== null && fees !== null ? round2(gross - fees) : null,
  };
}

/** One arm's resolved rows from `since` on, grouped by session in entry order; null when unreadable. */
function rowsOf(file: string, arm: string, since: string | null): Map<string, MeicLiveGapSide[]> | null {
  const outcome = readOnlyDb(file, (db) => {
    const armCol = findArmColumn(db, "ic_trades");
    if (armCol === null) return new Map<string, MeicLiveGapSide[]>();
    const rows = db
      .prepare<unknown[], Record<string, unknown>>(
        `SELECT trade_date, entry_time, symbol, put_strike, call_strike, wing_width, net_credit, status,
                pnl, fees, ${col(db, "quantity")} AS quantity
           FROM ic_trades
          WHERE ${armCol} = ? AND ${RESOLVED} ${since !== null ? "AND trade_date >= ?" : ""}
          ORDER BY trade_date, entry_time`,
      )
      .all(...(since !== null ? [arm, since] : [arm]));
    const out = new Map<string, MeicLiveGapSide[]>();
    for (const r of rows) {
      const day = str(r["trade_date"]);
      if (day === null) continue;
      out.set(day, [...(out.get(day) ?? []), side(r)]);
    }
    return out;
  });
  return outcome.status === "ok" ? outcome.value : null;
}

/**
 * One session's pairs: a live condor with the paper condor on the same strikes first, then what is
 * left by entry order, then the unpaired either side. On 2026-10-15 two condors a side entered
 * minutes apart, and entry order alone paired each with the other's strikes.
 */
export function pairsOf(
  live: MeicLiveGapSide[],
  paper: MeicLiveGapSide[],
): Array<[MeicLiveGapSide | null, MeicLiveGapSide | null]> {
  const key = (s: MeicLiveGapSide) => `${s.symbol}|${s.putStrike}|${s.callStrike}`;
  const left = [...paper];
  const out: Array<[MeicLiveGapSide | null, MeicLiveGapSide | null]> = [];
  const rest: MeicLiveGapSide[] = [];
  for (const a of live) {
    const i = left.findIndex((b) => key(b) === key(a));
    if (i >= 0) out.push([a, left.splice(i, 1)[0] ?? null]);
    else rest.push(a);
  }
  for (const a of rest) out.push([a, left.shift() ?? null]);
  for (const b of left) out.push([null, b]);
  return out.sort((x, y) => String((x[0] ?? x[1])?.entry).localeCompare(String((y[0] ?? y[1])?.entry)));
}

const NO_TOTALS ={ paired: 0, liveNet: 0, paperNet: 0, gap: 0, paperOnly: 0, paperOnlyNet: 0, liveOnly: 0 };

export function readMeicLiveGap(config: ConsoleConfig): MeicLiveGapPayload {
  const dir = config.paths.meicDir;
  const live = rowsOf(path.join(dir, "meic_trades.db"), LIVE_ARM, null);
  const empty: MeicLiveGapPayload = { twin: TWIN_ARM, ledger: "absent", since: null, rows: [], totals: NO_TOTALS };
  if (live === null) return empty;
  const since = [...live.keys()].sort()[0] ?? null;
  const paper = since === null ? new Map<string, MeicLiveGapSide[]>() : rowsOf(path.join(dir, "paper_trades.db"), TWIN_ARM, since);
  if (paper === null) return empty;

  const rows: MeicLiveGapRow[] = [];
  const totals = { ...NO_TOTALS };
  const sessions = [...new Set([...live.keys(), ...paper.keys()])].sort();
  for (const session of sessions) {
    for (const [a, b] of pairsOf(live.get(session) ?? [], paper.get(session) ?? [])) {
      const gap = a?.net != null && b?.net != null ? round2(a.net - b.net) : null;
      rows.push({ session, live: a, paper: b, gap });
      if (gap !== null && a?.net != null && b?.net != null) {
        totals.paired += 1;
        totals.liveNet += a.net;
        totals.paperNet += b.net;
      } else if (a === null && b !== null) {
        totals.paperOnly += 1;
        totals.paperOnlyNet += b.net ?? 0;
      } else if (b === null && a !== null) {
        totals.liveOnly += 1;
      }
    }
  }
  totals.liveNet = round2(totals.liveNet);
  totals.paperNet = round2(totals.paperNet);
  totals.gap = round2(totals.liveNet - totals.paperNet);
  totals.paperOnlyNet = round2(totals.paperOnlyNet);
  return { twin: TWIN_ARM, ledger: "ok", since, rows: rows.reverse(), totals };
}
