import type { ProfileBarsPayload } from "@console/shared";
import { buildComposite, buildProfile, defaultRowSize, type MarketComposite, type MarketProfile } from "../../lib/marketProfile";

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
}

/**
 * One row size for every column, so their rows line up: `defaultRowSize` of the first bar sent (the
 * oldest), which stays put while the same span is shown.
 */
export function buildView(payload: ProfileBarsPayload, compositeSessions: number): ProfileView | null {
  if (!payload.available || payload.bars.length === 0) return null;
  const rowSize = defaultRowSize(payload.bars[0]!.c);
  const opts = { rowSize, barSeconds: payload.barSeconds };
  const profiles = payload.sessions
    .map((session) => buildProfile(payload.bars, { ...opts, session }))
    .filter((p): p is MarketProfile => p !== null);
  if (profiles.length === 0) return null;
  const composite =
    compositeSessions > 0 ? buildComposite(payload.bars, { ...opts, sessions: payload.sessions.slice(-compositeSessions) }) : null;
  const rowPrices = profiles.flatMap((p) => [p.rows[0]!.price, p.rows.at(-1)!.price]);
  return { rowSize, profiles, composite, low: Math.min(...rowPrices), high: Math.max(...rowPrices) };
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
