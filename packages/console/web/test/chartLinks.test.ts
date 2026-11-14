import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chartHref } from "../src/pages/Chart/chartRoute";

/**
 * Every link to a chart goes through `chartHref`, so the one chart's URL is spelt in one place. The old
 * tabs (`/charts/intraday`, `/charts/technicals`, `/charts/profile`) are reached only by their
 * redirects in `App.tsx`; a page that still linked to one would bounce through a redirect on every
 * click, and one that wrote `/charts/chart?` by hand would drift from the page's own rules.
 */

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
/** The redirects and the helper themselves, and comments naming the old routes. */
const ALLOWED = new Set(["App.tsx", "pages/Chart/chartRoute.ts"]);
/** An old chart route, or the one chart's, opened in a string or template literal. */
const HAND_BUILT = /["'`]\/charts\/(?:intraday|technicals|profile|chart)\?/;
/** A line of a block comment, a line comment or a JSX comment. */
const COMMENT = /^\s*(?:\*|\/\/|\/\*|\{\/\*)/;

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    return d.isDirectory() ? sources(p) : /\.tsx?$/.test(d.name) ? [p] : [];
  });
}

describe("links to a chart", () => {
  it("are built by chartHref everywhere but the redirects", () => {
    const offenders = sources(SRC)
      .map((f) => path.relative(SRC, f).replace(/\\/g, "/"))
      .filter((rel) => !ALLOWED.has(rel))
      .flatMap((rel) =>
        fs
          .readFileSync(path.join(SRC, rel), "utf-8")
          .split("\n")
          // A comment may show a URL (`/charts/chart?symbol=/ES&tf=5m`); only code builds one.
          .flatMap((line, i) => (HAND_BUILT.test(line) && !COMMENT.test(line) ? [`${rel}:${i + 1}`] : [])),
      );
    expect(offenders).toEqual([]);
  });

  it("name the symbol and timeframe first, keep a future's slash, and carry the view's keys in order", () => {
    expect(chartHref("MSFT", "1d")).toBe("/charts/chart?symbol=MSFT&tf=1d");
    expect(chartHref("/ES", "15m", { day: "2026-11-13" })).toBe("/charts/chart?symbol=/ES&tf=15m&day=2026-11-13");
    expect(chartHref("SPX", "1d", { setup: "trend", side: "short" })).toBe("/charts/chart?symbol=SPX&tf=1d&setup=trend&side=short");
    // Anything else a symbol could carry is still encoded.
    expect(chartHref("BRK B", "1d")).toBe("/charts/chart?symbol=BRK+B&tf=1d");
  });
});
