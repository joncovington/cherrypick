import { useEffect, useRef } from "react";

/**
 * Flashes the returned ref's element green/red when `value` changes, reusing the app's existing
 * flash-up/flash-down keyframes (styles.css) -- the same treatment LiveQuoteRow already gives live
 * quotes, generalized for any polled numeric value. Scoped to the highest-attention, tightest-poll
 * spots (GEX metrics, Overview's stat-tiles) rather than applied app-wide, so the dashboard doesn't
 * flicker on every refresh.
 */
export function useFlashOnChange<T extends HTMLElement>(value: number | null | undefined) {
  const ref = useRef<T>(null);
  const prev = useRef<number | null | undefined>(value);

  useEffect(() => {
    const el = ref.current;
    const prevValue = prev.current;
    prev.current = value;
    if (el === null || value == null || prevValue == null || value === prevValue) return;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth; // restart the animation
    el.classList.add(value > prevValue ? "flash-up" : "flash-down");
  }, [value]);

  return ref;
}
