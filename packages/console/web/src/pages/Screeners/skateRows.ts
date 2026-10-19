import type { SkateBand, SkateBandRead, SkateBest, SkateLeg, SkateRow, SkateSide } from "@console/shared";

/**
 * The Skate tab's row logic, kept apart from the component so it is tested on its own. It selects
 * and orders; every figure and every verdict it reads is the run file's (`technicals.skate`).
 *
 * - A row is one name-side on the chosen band: that band's pick leg on the band's best expiry, shown
 *   only when the leg passed the hard filters (`hard`). The best expiry is the file's; nothing here
 *   re-ranks expiries.
 * - The soft thresholds (`soft.all`) are a marker, never a filter, except under the "passing only"
 *   toggle, which the user sets.
 * - Default order: history minus implied (`gap`), widest first; a leg with no gap sorts last.
 */

export interface SkateView {
  row: SkateRow;
  band: SkateBand;
  best: SkateBest;
  pick: SkateLeg;
  /** The other band's read, for the quieter column. */
  other: SkateBandRead;
}

export const otherBand = (b: SkateBand): SkateBand => (b === "fixed" ? "byExpiry" : "fixed");

/** The band's pick on its best expiry, when one exists and passed the hard filters. */
function shown(row: SkateRow, band: SkateBand): SkateView | null {
  const read = row.bands[band];
  if (read.best === null || read.pick === null || !read.pick.hard) return null;
  return { row, band, best: read.best, pick: read.pick, other: row.bands[otherBand(band)] };
}

export function skateViews(rows: SkateRow[], side: SkateSide, band: SkateBand, passingOnly: boolean): SkateView[] {
  return rows
    .filter((r) => r.side === side)
    .map((r) => shown(r, band))
    .filter((v): v is SkateView => v !== null && (!passingOnly || v.pick.soft.all))
    .sort((a, b) => {
      const ga = a.pick.gap;
      const gb = b.pick.gap;
      if (ga === null && gb === null) return a.row.symbol.localeCompare(b.row.symbol);
      if (ga === null) return 1;
      if (gb === null) return -1;
      return gb - ga || a.row.symbol.localeCompare(b.row.symbol);
    });
}

export interface SkateCounts {
  /** Name-sides the run scored on this side. */
  read: number;
  /** Of those, with a hard-passing pick on the band. */
  tradable: number;
  /** Of those, also passing every soft threshold. */
  passing: number;
}

export function skateCounts(rows: SkateRow[], side: SkateSide, band: SkateBand): SkateCounts {
  const mine = rows.filter((r) => r.side === side);
  const views = mine.map((r) => shown(r, band)).filter((v): v is SkateView => v !== null);
  return { read: mine.length, tradable: views.length, passing: views.filter((v) => v.pick.soft.all).length };
}

/** The best expiry pays more a day than the monthly pick (the file's ratio). */
export const beatsMonthly = (b: SkateBest): boolean => b.vsMonthly !== null && b.vsMonthly > 1;
