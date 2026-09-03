/**
 * Session-clock helpers for the hand-SVG chart family -- extracted 2026-09 from byte-identical
 * copies in `Flies/TimelineCard.tsx` and `Flies/JournalCard.tsx`.
 */

/** Minutes since midnight, read straight off an ISO-ish timestamp's `HH:MM` (chars 11-16) --
 *  cheap and exact for the ET wall-clock strings every ledger here writes; no timezone math. */
export const minuteOf = (ts: string): number => {
  const hm = ts.slice(11, 16);
  return Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5));
};

/** Minutes since midnight back to `HH:MM`. */
export const hhmm = (m: number): string =>
  `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
