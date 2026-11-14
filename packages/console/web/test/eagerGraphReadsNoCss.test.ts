import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * `main.tsx` imports `App` before `styles.css`, and every page manifest is lazy, so a chart module that
 * reads a CSS variable at import time (`const PAL = canvasPalette()`) is safe only while nothing `App`
 * imports eagerly reaches it. Reached early, `cssvars` throws ("--text-muted is not defined in :root")
 * and the whole console renders blank, which no unit test sees: they run without the stylesheet. This
 * walks App's static imports (a dynamic `import()` is where the lazy pages begin) and fails on any
 * module-level CSS read it finds.
 */

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const STATIC_IMPORT = /^\s*(?:import|export)\s[^;]*?from\s+["'](\.{1,2}\/[^"']+)["']/gm;
const BARE_IMPORT = /^\s*import\s+["'](\.{1,2}\/[^"']+)["']/gm;
/** A top-level const whose value calls one of the CSS readers in `components/chart/cssvars`. */
const MODULE_LEVEL_CSS_READ = /^(?:export\s+)?const\s+\w+\s*(?::[^=]+)?=\s*(?:_pal|canvasPalette|hexOf|tokenAlpha|resolveColor)\(/m;

function resolve(from: string, spec: string): string | null {
  const base = path.resolve(path.dirname(from), spec);
  for (const c of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return null;
}

function eagerGraph(entry: string): Set<string> {
  const seen = new Set<string>();
  const stack = [entry];
  while (stack.length > 0) {
    const file = stack.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = fs.readFileSync(file, "utf-8");
    for (const re of [STATIC_IMPORT, BARE_IMPORT]) {
      for (const m of text.matchAll(re)) {
        // A type-only import is erased and evaluates nothing.
        if (/^\s*(?:import|export)\s+type\s/.test(m[0])) continue;
        const next = resolve(file, m[1]!);
        if (next !== null && /\.tsx?$/.test(next)) stack.push(next);
      }
    }
  }
  return seen;
}

describe("the app shell's eager imports", () => {
  it("reach no module that reads a CSS variable at import time", () => {
    const graph = eagerGraph(path.join(SRC, "App.tsx"));
    expect(graph.size).toBeGreaterThan(5);
    const offenders = [...graph]
      .filter((f) => MODULE_LEVEL_CSS_READ.test(fs.readFileSync(f, "utf-8")))
      .map((f) => path.relative(SRC, f).replace(/\\/g, "/"));
    expect(offenders).toEqual([]);
  });

  it("would catch a chart page pulled in eagerly", () => {
    // The regex itself: IntradayPage reads the palette at module level, so it must match.
    expect(MODULE_LEVEL_CSS_READ.test(fs.readFileSync(path.join(SRC, "pages", "Intraday", "IntradayPage.tsx"), "utf-8"))).toBe(true);
  });
});
