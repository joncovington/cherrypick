import type Database from "better-sqlite3";
import type { LiveRiskSession, OnPeakRisk } from "@console/shared";
import { hasColumn, num, str } from "./db.js";
import { PEAK_ROW_COLUMNS, peakRisk, peakRowFrom, type PeakRow } from "../analytics/fliesPeakRisk.js";

/**
 * Return on peak risk, per session, for any flies ledger: settled net (core.ledgers' flies rule,
 * `gross_pnl - fees` over `status = 'settled'`) against the session's peak open worst case. The
 * peak is the live loop's recorded `fly_live_marks.open_margin` where a session has one, and the
 * replay in `analytics/fliesPeakRisk.ts` everywhere else -- every paper session, and a live
 * session from before the marks table. The two agree to the cent wherever both exist.
 *
 * Recorded marks are the loop's whole book, not one arm's: the live pilot runs one arm a day. A
 * paper ledger has no marks, so each arm is replayed from its own positions alone -- the arms are
 * independent portfolios, and one arm's peak is never another's.
 */
export interface PeakScope {
  arm: string | null;
  /** Undefined: every row. Null: rows with no experiment stamp. A string: rows stamped with it. */
  experimentId?: string | null;
  start?: string | null;
  end?: string | null;
}

function hasTable(db: Database.Database, name: string): boolean {
  return db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name) !== undefined;
}

export function peakRiskSessions(db: Database.Database, scope: PeakScope): LiveRiskSession[] {
  if (!hasTable(db, "fly_positions")) return [];
  const where: string[] = ["COALESCE(status, '') NOT IN ('cancelled', 'voided')"];
  const params: string[] = [];
  if (scope.arm !== null) {
    where.push("arm = ?");
    params.push(scope.arm);
  }
  if (scope.experimentId !== undefined && hasColumn(db, "fly_positions", "experiment_id")) {
    if (scope.experimentId === null) where.push("experiment_id IS NULL");
    else {
      where.push("experiment_id = ?");
      params.push(scope.experimentId);
    }
  }
  if (scope.start != null) {
    where.push("trade_date >= ?");
    params.push(scope.start);
  }
  if (scope.end != null) {
    where.push("trade_date <= ?");
    params.push(scope.end);
  }
  const cols = PEAK_ROW_COLUMNS.map((c) => (hasColumn(db, "fly_positions", c) ? c : `NULL AS ${c}`)).join(", ");
  const rows = db
    .prepare<string[], Record<string, unknown>>(
      `SELECT trade_date, gross_pnl, fees AS row_fees, ${cols} FROM fly_positions WHERE ${where.join(" AND ")}`,
    )
    .all(...params);

  const days = new Map<string, { s: LiveRiskSession; book: PeakRow[] }>();
  for (const r of rows) {
    const d = String(r["trade_date"] ?? "");
    if (d === "") continue;
    let day = days.get(d);
    if (day === undefined) {
      day = {
        s: { session: d, trades: 0, net: 0, peakRisk: null, peakAt: null, peakSource: null, onRisk: null, complete: true },
        book: [],
      };
      days.set(d, day);
    }
    const status = str(r["status"]);
    if (status === "settled") {
      day.s.trades += 1;
      day.s.net += (num(r["gross_pnl"]) ?? 0) - (num(r["row_fees"]) ?? 0);
    } else if (status !== "closed") {
      day.s.complete = false;
    }
    day.book.push(peakRowFrom(r));
  }

  const recorded = new Map<string, { peak: number; at: string | null }>();
  if (hasTable(db, "fly_live_marks")) {
    // One row per day: SQLite returns the bare `iteration_ts` from the row MAX() chose.
    for (const r of db
      .prepare<[], Record<string, unknown>>(
        "SELECT trade_date, iteration_ts, MAX(open_margin) AS peak FROM fly_live_marks GROUP BY trade_date",
      )
      .all()) {
      const peak = num(r["peak"]);
      if (peak !== null && peak > 0) recorded.set(String(r["trade_date"] ?? ""), { peak, at: str(r["iteration_ts"]) });
    }
  }

  const out: LiveRiskSession[] = [];
  for (const [d, { s, book }] of days) {
    const rec = recorded.get(d);
    if (rec !== undefined) {
      s.peakRisk = rec.peak;
      s.peakAt = rec.at;
      s.peakSource = "recorded";
    } else {
      const rp = peakRisk(book);
      // A book of nothing but risk-free structures peaks at zero: a ratio over it is undefined.
      if (rp.peak > 0) {
        s.peakRisk = rp.peak;
        s.peakAt = rp.at;
        s.peakSource = "replayed";
      }
    }
    s.onRisk = s.complete && s.trades > 0 && s.peakRisk !== null ? s.net / s.peakRisk : null;
    out.push(s);
  }
  return out.sort((a, b) => b.session.localeCompare(a.session));
}

/** Σ net / Σ peak over the finished sessions in `bounds` that have a peak. The numerator is matched
 *  to the denominator: a session with no peak is counted in `of`, never in either. */
export function onPeakRiskOver(sessions: LiveRiskSession[], bounds: [string, string] | null = null): OnPeakRisk {
  const inScope = sessions.filter((s) => s.trades > 0 && (bounds === null || (s.session >= bounds[0] && s.session <= bounds[1])));
  const covered = inScope.filter((s) => s.complete && s.peakRisk !== null);
  const net = covered.reduce((a, s) => a + s.net, 0);
  const peak = covered.reduce((a, s) => a + (s.peakRisk ?? 0), 0);
  return {
    net,
    peakRisk: peak,
    ratio: peak > 0 ? net / peak : null,
    sessions: covered.length,
    of: inScope.length,
    replayed: covered.filter((s) => s.peakSource === "replayed").length,
  };
}
