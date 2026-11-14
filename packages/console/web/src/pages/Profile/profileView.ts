import type { CandleBar, ProfileBarsPayload } from "@console/shared";
import {
  buildComposite,
  buildProfile,
  rowSizeFor,
  sessionDates,
  type MarketComposite,
  type MarketProfile,
} from "../../lib/marketProfile";

/**
 * What the Profile tab draws, from the recorded bars: every session's profile and a composite of the
 * newest `compositeSessions` of them, all on one row grid. Built by `lib/marketProfile` (the mirror of
 * `cherrypick.core.marketprofile`); this module only chooses the sessions and the grid.
 */

export interface ProfileView {
  rowSize: number;
  profiles: MarketProfile[];
  composite: MarketComposite | null;
  /** Lowest and highest row price over everything drawn. */
  low: number;
  high: number;
  /** Sessions built from the live candles because the store does not hold them yet (today, and any
   *  session the 17:15 job has not recorded), newest last. */
  live: string[];
}

/**
 * Every session's profile, the recorded ones and then any the live candles hold beyond the newest of
 * them, on one row grid: the symbol's fixed `rowSizeFor` (the intraday chart's and the morning pack's), or for a
 * symbol without one `defaultRowSize` of the first bar (the oldest), which stays put while the same
 * span is shown. Live bars are used only after the newest recorded bar, never mixed with recorded
 * minutes of the same time. The live series is 15-minute; every 15-minute bar also starts on the
 * recorded 5-minute grid, so one bar width serves both.
 */
export function buildView(
  payload: ProfileBarsPayload,
  compositeSessions: number,
  liveBars: readonly CandleBar[] = [],
): ProfileView | null {
  const recorded = payload.available ? payload.bars : [];
  const lastRecorded = recorded.at(-1)?.t ?? -Infinity;
  const newestRecorded = payload.available ? (payload.sessions.at(-1) ?? "") : "";
  const after = liveBars.filter((b) => b.t > lastRecorded);
  const live = sessionDates(after).filter((d) => d > newestRecorded);
  const bars = [...recorded, ...after];
  if (bars.length === 0) return null;
  const sessions = [...(payload.available ? payload.sessions : []), ...live];
  const rowSize = rowSizeFor(payload.symbol, bars[0]!.c);
  const opts = { rowSize, barSeconds: payload.barSeconds };
  const profiles = sessions
    .map((session) => buildProfile(bars, { ...opts, session }))
    .filter((p): p is MarketProfile => p !== null);
  if (profiles.length === 0) return null;
  const composite = compositeSessions > 0 ? buildComposite(bars, { ...opts, sessions: sessions.slice(-compositeSessions) }) : null;
  const rowPrices = profiles.flatMap((p) => [p.rows[0]!.price, p.rows.at(-1)!.price]);
  return { rowSize, profiles, composite, low: Math.min(...rowPrices), high: Math.max(...rowPrices), live };
}

/** Every period of a full regular session, A (09:30) to M (15:30). */
export const FULL_SESSION = "ABCDEFGHIJKLM";

/**
 * The periods a session holds when it is not all of A–M, as "H–M" (or "G" alone), else null. The oldest
 * recorded session can start mid-day, where the feed's 8,000-bar cap cut it; a half-day closes at G.
 * Its profile is real but covers less of the day, which the tab says rather than hides.
 */
export function partialPeriods(p: MarketProfile): string | null {
  if (p.periods === FULL_SESSION) return null;
  return p.periods.length > 1 ? `${p.periods[0]}–${p.periods.at(-1)}` : p.periods;
}

export type ValueRelation = "higher" | "lower" | "overlapping";

/**
 * Where a session's value area sits against the prior session's: wholly above it (higher), wholly
 * below it (lower), or sharing any price with it (overlapping). Value areas are compared by their
 * rows' outer edges (VAL's lower edge, VAH's upper edge). A display reading, not a signal.
 */
export function valueRelation(prior: MarketProfile, cur: MarketProfile): ValueRelation {
  const priorTop = prior.vah + prior.rowSize;
  const curTop = cur.vah + cur.rowSize;
  if (cur.val >= priorTop) return "higher";
  if (curTop <= prior.val) return "lower";
  return "overlapping";
}
