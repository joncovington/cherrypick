import type { DatabaseHandle } from "./db.js";
import { num, str } from "./db.js";

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
 * for a leg sold to open and `close - entry` for one bought. This is that arithmetic with the leg's
 * CURRENT usable mark standing in for `close_value`, so an open row and a closed row mean the same
 * thing by the same formula rather than by two that happen to agree.
 *
 * Written once here rather than three times: the convention is identical across the modules, so a
 * per-module copy would be two chances to drift on a definition neither owns alone. bwb and curve
 * are the exceptions and keep their own: their marks carry a whole-structure `close_cost` rather
 * than per-leg mids, so the same convention is reached by a shorter route there (credit + cost to
 * close), and bwb additionally has an add-on credit to add back. The split is by MARK SHAPE, not by
 * preference.
 *
 * `fees` is what has been INCURRED so far (entry, plus any roll or partial exit). No settlement fee
 * is in it, because settlement has not happened -- so net here is net of costs TO DATE, not of the
 * round trip, and every caller says so on the column rather than leaving it to be assumed.
 *
 * A position with any unpriceable open leg returns nulls: a partial mark is not a P&L, and a zero
 * standing in for "unknown" is the misleadingly-precise zero this suite's ledgers refuse to write.
 */
export function unrealisedByPosition(
  db: DatabaseHandle,
  opts: { positionsTable: string; legsTable: string; marksTable: string },
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

  // The latest USABLE mark per (position, leg). A refused mark is a recorded row, not a price.
  const marks = new Map<string, number>();
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT m.position_id, m.leg_role, m.mid FROM ${opts.marksTable} m
       JOIN (SELECT position_id, leg_role, MAX(marked_at) AS t FROM ${opts.marksTable}
             WHERE usable = 1 AND mid IS NOT NULL GROUP BY position_id, leg_role) x
         ON x.position_id = m.position_id AND x.leg_role = m.leg_role AND x.t = m.marked_at`,
    )
    .all()) {
    const mid = num(r["mid"]);
    if (mid !== null) marks.set(`${str(r["position_id"])} ${str(r["leg_role"])}`, mid);
  }

  const perPosition = new Map<string, { total: number; missing: boolean }>();
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT position_id, leg_role, action, entry_mid FROM ${opts.legsTable} WHERE status = 'open'`,
    )
    .all()) {
    const id = str(r["position_id"]) ?? "";
    if (!meta.has(id)) continue;
    const acc = perPosition.get(id) ?? { total: 0, missing: false };
    const entry = num(r["entry_mid"]);
    const mark = marks.get(`${id} ${str(r["leg_role"])}`);
    if (entry === null || mark === undefined) {
      acc.missing = true;
    } else {
      // leg_pnl: sold legs earn entry - close, bought legs earn close - entry.
      acc.total += str(r["action"]) === "Sell to Open" ? entry - mark : mark - entry;
    }
    perPosition.set(id, acc);
  }

  for (const [id, { fees, qty }] of meta) {
    const acc = perPosition.get(id);
    if (acc === undefined || acc.missing) {
      out.set(id, { ...NO_UNREALISED, feesToDate: fees });
      continue;
    }
    const gross = Math.round(acc.total * 100 * qty * 100) / 100;
    out.set(id, {
      unrealisedGross: gross,
      unrealisedNet: fees === null ? null : Math.round((gross - fees) * 100) / 100,
      feesToDate: fees,
    });
  }
  return out;
}
