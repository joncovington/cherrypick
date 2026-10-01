import { useEffect, useState } from "react";
import type { TradingMode } from "@console/shared";

export interface BookRotation {
  book: TradingMode;
  /** Show this book now; the clock restarts from here. */
  show: (book: TradingMode) => void;
  /** Hold still while true (a card being read should not change under the pointer). */
  setPaused: (paused: boolean) => void;
}

/**
 * Paper, then live, then paper: one clock shared by every card handed it, so the Overview's two
 * desk cards always show the same book. Any change of book or pause restarts the interval, so a
 * click or a hover never leaves a part-spent timer to flip the card a moment later.
 */
export function useBookRotation(intervalMs = 15_000): BookRotation {
  const [book, setBook] = useState<TradingMode>("paper");
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => setBook((b) => (b === "paper" ? "live" : "paper")), intervalMs);
    return () => clearInterval(t);
  }, [book, paused, intervalMs]);
  return { book, show: setBook, setPaused };
}
