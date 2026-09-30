import { positionFloor, stateAt, type FlyRow } from "./fliesPayoff.js";

/**
 * A session's peak risk: the largest open worst-case exposure its book carried at any moment --
 * `max(0, -floor)` summed over every position on the book, the sum the live loop records each tick
 * as `fly_live_marks.open_margin` and its buying-power cap reads.
 *
 * Only the live loop records that figure. Everywhere else it is REPLAYED from the positions: a
 * book's exposure only changes when a position enters, completes or closes, so the peak is the
 * largest sum at one of those instants. A position in its final state contributes the floor the
 * module recorded for it (`floor_dollars`) -- its `fees` by then include settlement, which had not
 * happened intraday, and counting them once put a completed fly at -$6.89 of risk it never
 * carried. A position before its completing leg is rewound by `stateAt` and priced by
 * `positionFloor`, the ports of the module's own `_state_at` and `position_floor`.
 *
 * Checked against every live session with a recorded peak (2026-09-18..09-29, eight sessions): the
 * replay equals the recorded peak to the cent on all eight.
 */
export interface PeakRow extends FlyRow {
  exitTime: string | null;
  floorDollars: number | null;
}

export function peakRisk(rows: PeakRow[]): { peak: number; at: string | null } {
  const events = [
    ...new Set(rows.flatMap((r) => [r.entryTime, r.completedAt]).filter((t): t is string => t !== null)),
  ].sort();
  let peak = 0;
  let at: string | null = null;
  for (const t of events) {
    let sum = 0;
    for (const r of rows) {
      // A position closed before the bell is off the book from its exit on.
      if (r.status === "closed" && r.exitTime !== null && t >= r.exitTime) continue;
      const s = stateAt(r, t);
      if (s === null) continue;
      const rewound = r.completedAt !== null && t < r.completedAt;
      const floor = !rewound && r.floorDollars !== null ? r.floorDollars : positionFloor(s);
      sum += Math.max(0, -floor);
    }
    if (sum > peak) {
      peak = sum;
      at = t;
    }
  }
  return { peak, at };
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function strOrNull(v: unknown): string | null {
  return v === null || v === undefined ? null : String(v);
}

/** The columns `peakRowFrom` reads. Callers substitute `NULL AS <col>` for any a ledger lacks. */
export const PEAK_ROW_COLUMNS = [
  "kind", "side", "center", "wing_width", "far_width", "net", "quantity", "fees", "status", "symbol",
  "entry_time", "completed_at", "entry_mode", "credit", "debit", "exit_time", "floor_dollars",
] as const;

export function peakRowFrom(r: Record<string, unknown>): PeakRow {
  return {
    kind: String(r["kind"] ?? "fly"),
    side: String(r["side"] ?? "put"),
    center: Number(r["center"]),
    wingWidth: Number(r["wing_width"]),
    farWidth: numOrNull(r["far_width"]),
    net: Number(r["net"] ?? 0),
    quantity: Number(r["quantity"] ?? 1),
    fees: Number(r["fees"] ?? 0),
    status: strOrNull(r["status"]),
    settlementPrice: null,
    symbol: String(r["symbol"] ?? "SPX"),
    entryTime: strOrNull(r["entry_time"]),
    completedAt: strOrNull(r["completed_at"]),
    entryMode: strOrNull(r["entry_mode"]),
    credit: numOrNull(r["credit"]),
    debit: numOrNull(r["debit"]),
    exitTime: strOrNull(r["exit_time"]),
    floorDollars: numOrNull(r["floor_dollars"]),
  };
}
