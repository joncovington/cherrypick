import { useEffect, useRef, type RefObject } from "react";
import type { QuoteState } from "./wsClient";

/** The flash's length in styles.css (`.flash-up`, `.flash-down`). */
export const FLASH_MS = 500;
/** A tick older than this is a backlog, not the tape: the server stamps each tick as it sends it,
 *  so a quote that arrives later than this sat in a queue (a sleeping tab) and has no move to show. */
export const STALE_TICK_MS = 2_000;

/**
 * Which flash a new tick earns, if any. Pure, so the rules are testable without a DOM.
 *
 * A sleeping tab (Chrome freezes a background tab) queues its socket messages and delivers them all
 * at once on wake; flashing each one ran the chips through minutes of ticks in a second. So a tick
 * flashes only when it is live (`dxlink`), has a direction, arrives while the page is visible, is
 * fresh, and no flash started in the last `FLASH_MS` (one is already showing).
 */
export function flashFor(input: {
  q: QuoteState;
  now: number;
  lastFlashAt: number;
  hidden: boolean;
}): "up" | "down" | null {
  const { q, now, lastFlashAt, hidden } = input;
  if (q.source !== "dxlink" || q.direction === null || hidden) return null;
  if (now - q.ts > STALE_TICK_MS) return null;
  if (now - lastFlashAt < FLASH_MS) return null;
  return q.direction;
}

/**
 * Flash `ref`'s element green or red on each new tick of `q`, by `flashFor`'s rules. `enabled`
 * false (a position card out of the session) never flashes. Used by the top bar's futures chips
 * and the position cards' spot.
 */
export function useTickFlash(ref: RefObject<HTMLElement | null>, q: QuoteState | undefined, enabled = true): void {
  const prevTs = useRef(0);
  const lastFlashAt = useRef(0);
  useEffect(() => {
    if (q === undefined || q.ts === prevTs.current) return;
    prevTs.current = q.ts;
    const el = ref.current;
    if (el === null || !enabled) return;
    const now = Date.now();
    const dir = flashFor({ q, now, lastFlashAt: lastFlashAt.current, hidden: document.hidden });
    if (dir === null) return;
    lastFlashAt.current = now;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth; // restart the animation
    el.classList.add(dir === "up" ? "flash-up" : "flash-down");
  }, [ref, q, enabled]);
}
