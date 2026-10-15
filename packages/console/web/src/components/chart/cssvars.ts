/**
 * The one runtime reader for the stylesheet's tokens in canvas contexts.
 *
 * The hand-SVG charts can paint `var(--pos)` and the browser resolves it against the stylesheet;
 * the lightweight-charts views (PerformanceTab, ChartPage, IntradayPage and the like) cannot — a
 * canvas knows nothing of custom properties. Every surface a canvas paints goes through this
 * one `getHex`, read once at chart construction, so the palette lives in `:root` and nowhere
 * else. If a token ever moves, this fails visibly (a thrown error, not a silent wrong colour),
 * and the light theme will re-derive both the stylesheet and these reads with nothing to change
 * here.
 */
export function getHex(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!v) throw new Error(`cssvars: --${name.slice(2)} is not defined in :root — add it to styles.css`);
  return v;
}

/** The canvas-chart palette, read through getHex. Import `CANVAS` and destructure. */
export function canvasPalette() {
  return {
    textMuted: getHex("--text-muted", "#9ca3af"),
    grid: getHex("--chart-grid", "#15181e"),
    gridMid: getHex("--chart-grid-mid", "#23262d"),
    pos: getHex("--pos", "#3af3a0"),
    neg: getHex("--neg", "#ee6760"),
    amber: getHex("--amber-line", "#f59e0b"),
    accent: getHex("--accent", "#3761e1"),
  };
}

/** Like getHex, but a missing token returns the fallback instead of throwing — for colourways
 *  whose tokens may arrive later than the chart (module-level consts read at import time). */
export function hexOf(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Resolve a colour the stylesheet might have written as a var() into the hex a canvas needs.
 *  Anything not a var() — a hex, a rgba(), a color-mix the canvas could never read either —
 *  passes through unchanged; callers must not hand this a color-mix and expect it to resolve. */
export function resolveColor(c: string): string {
  if (!c.startsWith("var(--")) return c;
  const end = c.indexOf(")");
  if (end === -1) return c;
  const name = c.slice(4, end); // "--pos"
  return hexOf(name, "#ff00ff"); // a magenta miss is a loud defect, never a silent wrong colour
}
