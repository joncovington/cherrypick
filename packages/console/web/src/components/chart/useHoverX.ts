/**
 * The horizontal-hover wiring shared by the hand-SVG chart family -- extracted 2026-09 from
 * byte-identical `onMouseMove` bodies in ForestCard.tsx, MeicForestCard.tsx and TimelineCard.tsx:
 * each computed `fx` from `clientX`/`getBoundingClientRect`, gated it to `[padLeft, width -
 * padRight]`, and stored either `fx` itself or a value derived from it. JournalCard's hover is a
 * per-bar `onMouseEnter` (an index, not a pointer position) and does not use this.
 *
 * Callers that stored something other than raw `fx` (ForestCard kept a `frac`, MeicForestCard a
 * `price`) now derive that value from `fx` inline instead of duplicating the gate/compute logic.
 */
import { useState, useCallback, useRef } from "react";

export function useHoverX(width: number, padLeft: number, padRight: number) {
  const [fx, setFx] = useState<number | null>(null);
  // Re-created every render otherwise (width/pad are stable per chart, but not by reference across
  // renders) -- a ref keeps the JSX call sites free of a `useCallback` dependency array to get wrong.
  const bounds = useRef({ width, padLeft, padRight });
  bounds.current = { width, padLeft, padRight };

  const onMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    const { width, padLeft, padRight } = bounds.current;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * width;
    setFx(x >= padLeft && x <= width - padRight ? x : null);
  }, []);

  const onMouseLeave = useCallback(() => setFx(null), []);

  return { fx, onMouseMove, onMouseLeave };
}
