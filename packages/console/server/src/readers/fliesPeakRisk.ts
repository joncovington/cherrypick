import type Database from "better-sqlite3";
import type { LiveRiskSession, OnPeakRisk } from "@console/shared";
import { hasColumn, num, str } from "./db.js";
import { PEAK_ROW_COLUMNS, peakRisk, peakRowFrom, type PeakRow } from "../analytics/fliesPeakRisk.js";

/**
 * Return on peak risk, per session, for any flies ledger: settled net (core.ledgers' flies rule,
 * `gross_pnl - fees` over `status = 'settled'`) against the session's peak open worst case. The
 * peak, in order of preference:
 *
 * 1. `booked` -- flies' own figure, recorded on each settled book (`fly_books.peak_risk`,
 *    `analytics.session_peak_risk`, backfilled for earlier books). It prices actual fills, and it
 *    is the figure `core.analysis` reads, so this slide and the analysis slide agree.
 * 2. `recorded` -- the live loop's `fly_live_marks.open_margin`, for a session not yet settled
 *    (today's, until the bell). It is what the buying-power cap reads, but it can hold an entry's
 *    limit price before a better fill is reconciled, and an order working at that tick that never
 *    filled: on 7 of 21 live sessions it was above the booked figure (flies' backfill names why).
 * 3. `replayed` -- the replay in `analytics/fliesPeakRisk.ts`, where neither exists.
 *
 * Books and marks are summed per session over the scope's arm (every arm when the scope names
 * none). Recorded marks are the loop's whole book: the live pilot runs one arm a day. Paper arms
 * are independent portfolios, and one arm's peak is never another's.
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
  // A voided row keeps `status = 'settled'`; only `void_reason` says it was struck.
  if (hasColumn(db, "fly_positions", "void_reason")) where.push("void_reason IS NULL");
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

  // Flies' own peak per settled book, summed per session over the scope's arm(s).
  const booked = new Map<string, { peak: number; at: string | null; largest: number }>();
  if (hasTable(db, "fly_books") && hasColumn(db, "fly_books", "peak_risk")) {
    const bookWhere = ["status = 'settled'", "peak_risk IS NOT NULL"];
    const bookParams: string[] = [];
    if (scope.arm !== null) {
      bookWhere.push("arm = ?");
      bookParams.push(scope.arm);
    }
    for (const r of db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, peak_risk, peak_risk_at FROM fly_books WHERE ${bookWhere.join(" AND ")}`,
      )
      .all(...bookParams)) {
      const d = String(r["trade_date"] ?? "");
      const peak = num(r["peak_risk"]) ?? 0;
      const prev = booked.get(d);
      // Two books on one session are two symbols side by side: summed, which can only overstate.
      // `at` is the largest single book's moment.
      const larger = prev === undefined || peak > prev.largest;
      booked.set(d, {
        peak: (prev?.peak ?? 0) + peak,
        at: larger ? str(r["peak_risk_at"]) : prev.at,
        largest: larger ? peak : prev.largest,
      });
    }
  }

  const out: LiveRiskSession[] = [];
  for (const [d, { s, book }] of days) {
    const bk = booked.get(d);
    const rec = recorded.get(d);
    if (bk !== undefined) {
      // A book that carried nothing at risk has no peak to divide by: null, never a zero.
      if (bk.peak > 0) {
        s.peakRisk = bk.peak;
        s.peakAt = bk.at;
        s.peakSource = "booked";
      }
    } else if (rec !== undefined) {
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
    booked: covered.filter((s) => s.peakSource === "booked").length,
  };
}
