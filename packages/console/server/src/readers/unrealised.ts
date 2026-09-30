import type { DatabaseHandle } from "./db.js";
import { hasColumn, num, str } from "./db.js";

/** Mark-to-market P&L for one open position, in the modules' shared convention. */
export interface Unrealised {
  unrealisedGross: number | null;
  unrealisedNet: number | null;
  feesToDate: number | null;
}

export const NO_UNREALISED: Unrealised = {
  unrealisedGross: null,
  unrealisedNet: null,
  feesToDate: null,
};

/**
 * Mark-to-market P&L for open positions, keyed by position id.
 *
 * pmcc and calendars state the SAME convention in their own `book.py`, near enough word
 * for word: "`gross_pnl` is mid-priced and cost-free: the sum of per-leg P&L (`engine.leg_pnl`)
 * x100 x qty" and "net is always `gross_pnl - fees`, one subtraction". `leg_pnl` is `entry - close`
 * for a leg sold to open and `close - entry` for one bought. This is that arithmetic -- the
 * modules' own `finalize_if_done` -- run on a position that has not closed yet:
 *
 *   - an OPEN leg is priced at its latest usable mark, standing in for the `close_value` it has
 *     not got yet;
 *   - a leg that has already SETTLED or closed is priced at its recorded `close_value`, exactly as
 *     finalize will. Until 2026-09-24 only open legs were read, so from Friday's settlement to
 *     Monday's 09:45 disposal a calendar week showed its back leg alone: the whole front credit
 *     ($100-$200 a position) was missing and 5 of 10 such SPY weeks showed the wrong sign;
 *   - a delivered share position still held (`assignmentsTable`, status open) is priced at the
 *     latest spot the module itself recorded on that position's marks, through
 *     `core.settlement.share_pnl`'s arithmetic. Those shares were invisible too: 2026-09-07's
 *     assigned SPY week rode the weekend with 100 shares the page never showed.
 *
 * Written once here rather than three times: the convention is identical across the modules, so a
 * per-module copy would be two chances to drift on a definition neither owns alone. bwb and curve
 * price OPEN legs their own way -- their marks carry a whole-structure `close_cost` rather than
 * per-leg mids -- and bwb additionally has an add-on credit to add back; once curve has no open leg
 * left it comes here like the others. The split is by MARK SHAPE, not by preference.
 *
 * `fees` is what has been INCURRED so far (entry, any roll or partial exit, and a cash settlement
 * fee once an ITM cash-settled leg has settled). A physical assignment's fees arrive at disposal,
 * so net here is net of costs TO DATE, not of the round trip, and every caller says so on the column.
 *
 * Anything unpriceable -- an open leg with no usable mark, a settled leg with no `close_value`, held
 * shares with no recorded spot -- returns nulls: a partial mark is not a P&L, and a zero standing
 * in for "unknown" is the misleadingly-precise zero this suite's ledgers refuse to write.
 */
export function unrealisedByPosition(
  db: DatabaseHandle,
  opts: { positionsTable: string; legsTable: string; marksTable: string; assignmentsTable?: string },
): Map<string, Unrealised> {
  const out = new Map<string, Unrealised>();
  const meta = new Map<string, { fees: number | null; qty: number }>();
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT position_id, fees, quantity FROM ${opts.positionsTable} WHERE status != 'closed'`,
    )
    .all()) {
    const id = str(r["position_id"]) ?? "";
    meta.set(id, { fees: num(r["fees"]), qty: num(r["quantity"]) ?? 1 });
  }
  if (meta.size === 0) return out;

  // Only the positions in `meta` are ever priced, so every marks read is restricted to them in SQL:
  // aggregating the whole marks table -- closed positions included, 221k rows on calendars --
  // cost ~350 ms a poll on the thread the heartbeat watches, for the same result.
  const openIds = `SELECT position_id FROM ${opts.positionsTable} WHERE status != 'closed'`;

  // The latest USABLE mark per (position, leg). A refused mark is a recorded row, not a price.
  const marks = new Map<string, number>();
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT m.position_id, m.leg_role, m.mid FROM ${opts.marksTable} m
       JOIN (SELECT position_id, leg_role, MAX(marked_at) AS t FROM ${opts.marksTable}
             WHERE usable = 1 AND mid IS NOT NULL AND position_id IN (${openIds})
             GROUP BY position_id, leg_role) x
         ON x.position_id = m.position_id AND x.leg_role = m.leg_role AND x.t = m.marked_at
       WHERE m.position_id IN (${openIds})`,
    )
    .all()) {
    const mid = num(r["mid"]);
    if (mid !== null) marks.set(`${str(r["position_id"])} ${str(r["leg_role"])}`, mid);
  }

  const perPosition = new Map<string, { total: number; missing: boolean; dollars: number }>();
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      // `SELECT *`: a ledger (or fixture) predating `close_value` must degrade to "settled legs
      // unpriced", not throw -- a throw here empties the caller's whole payload.
      `SELECT * FROM ${opts.legsTable}`,
    )
    .all()) {
    const id = str(r["position_id"]) ?? "";
    if (!meta.has(id)) continue;
    const acc = perPosition.get(id) ?? { total: 0, missing: false, dollars: 0 };
    const entry = num(r["entry_mid"]);
    const price =
      str(r["status"]) === "open" ? marks.get(`${id} ${str(r["leg_role"])}`) : (num(r["close_value"]) ?? undefined);
    if (entry === null || price === undefined) {
      acc.missing = true;
    } else {
      // leg_pnl: sold legs earn entry - close, bought legs earn close - entry.
      acc.total += str(r["action"]) === "Sell to Open" ? entry - price : price - entry;
    }
    perPosition.set(id, acc);
  }

  const assignments = opts.assignmentsTable;
  const haveShares =
    assignments !== undefined &&
    db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(assignments) !== undefined;
  if (assignments !== undefined && haveShares) {
    // The latest spot the module recorded on each position's own marks: the price its next mark
    // would read shares at. Its marks carry spot on every row, usable or not.
    const spots = new Map<string, number>();
    const spotRows = hasColumn(db as never, opts.marksTable, "spot") ? db
      .prepare<[], Record<string, unknown>>(
        `SELECT m.position_id, m.spot FROM ${opts.marksTable} m
         JOIN (SELECT position_id, MAX(marked_at) AS t FROM ${opts.marksTable}
               WHERE spot IS NOT NULL AND position_id IN (${openIds}) GROUP BY position_id) x
           ON x.position_id = m.position_id AND x.t = m.marked_at
         WHERE m.spot IS NOT NULL AND m.position_id IN (${openIds})`,
      )
      .all() : [];
    for (const r of spotRows) {
      const spot = num(r["spot"]);
      if (spot !== null) spots.set(str(r["position_id"]) ?? "", spot);
    }
    for (const r of db
      .prepare<[], Record<string, unknown>>(
        `SELECT position_id, direction, shares, basis FROM ${assignments} WHERE status = 'open'`,
      )
      .all()) {
      const id = str(r["position_id"]) ?? "";
      if (!meta.has(id)) continue;
      const acc = perPosition.get(id) ?? { total: 0, missing: false, dollars: 0 };
      const spot = spots.get(id);
      const basis = num(r["basis"]);
      const shares = num(r["shares"]);
      if (spot === undefined || basis === null || shares === null) {
        acc.missing = true;
      } else {
        // core.settlement.share_pnl: long earns the rise. Already dollars, so kept out of the
        // per-share leg total that is scaled by 100 x qty below.
        acc.dollars += (str(r["direction"]) === "long" ? spot - basis : basis - spot) * shares;
      }
      perPosition.set(id, acc);
    }
  }

  for (const [id, { fees, qty }] of meta) {
    const acc = perPosition.get(id);
    if (acc === undefined || acc.missing) {
      out.set(id, { ...NO_UNREALISED, feesToDate: fees });
      continue;
    }
    const gross = Math.round((acc.total * 100 * qty + acc.dollars) * 100) / 100;
    out.set(id, {
      unrealisedGross: gross,
      unrealisedNet: fees === null ? null : Math.round((gross - fees) * 100) / 100,
      feesToDate: fees,
    });
  }
  return out;
}
