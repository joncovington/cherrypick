import { useEffect, useRef, useState, type MutableRefObject } from "react";

/**
 * The width a chart actually has, rather than the width it was written for.
 *
 * Every hand-rolled SVG in this package hardcodes `width = 1150` and scales it with
 * `viewBox` + `width: 100%`. That works while every surface is one full-bleed column, which is
 * what `.cards-wide` made them. It stops working the moment a chart sits in a grid cell: the
 * drawing scales, so a 9px axis label rendered at a third of the width is 3px, and the tick
 * spacing `niceTicks` chose for 1150 is wrong for 380. Scaling is not reflowing.
 *
 * So a card-sized chart measures its own container and passes THAT into the same scale
 * functions. `fallback` is what callers get where there is nothing to measure -- a server
 * render, a test, a browser without `ResizeObserver` -- and it should be the width the chart was
 * designed at, so `viewBox` is always a real number and the SSR tests still see a drawn chart.
 */
export function useMeasure<T extends HTMLElement = HTMLDivElement>(
  fallback: number,
): [MutableRefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const el = ref.current;
    if (el === null || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      // A zero width is a hidden container (a collapsed card, a closed drawer), not a
      // measurement: keeping the last real width means reopening it does not flash a chart
      // drawn one pixel wide.
      if (w !== undefined && w > 0) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
    };
  }, []);

  return [ref, width];
}
