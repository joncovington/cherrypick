import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { builtinModules } from "node:module";
import { fileURLToPath } from "node:url";

/**
 * The SPA imports `@console/shared`'s barrel, and Vite's dev server loads every module the barrel
 * names -- the production build tree-shakes an unused one away, the dev server does not. So a Node
 * builtin anywhere under the barrel builds clean and passes every test here (vitest runs in Node),
 * then breaks any page that imports a runtime value from shared under `vite` dev: that is how
 * `paths.ts` (node:fs/os/path/url) took the MEIC page down until 2026-10-18. Node-only helpers live
 * behind their own subpath (`@console/shared/paths`); this walks the barrel's import graph from
 * `shared/src/index.ts` and fails on any builtin it reaches.
 */
const SHARED_SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "shared", "src");

const BUILTINS = new Set(builtinModules);
// Runtime imports and re-exports only: `import type` / `export type` are erased and load nothing.
const SPECIFIER = /^\s*(?:import|export)\s+(?!type\b)(?:[^"';]*?\s+from\s+)?["']([^"']+)["']/gm;

export function isBuiltin(spec: string): boolean {
  return spec.startsWith("node:") || BUILTINS.has(spec.split("/")[0] ?? spec);
}

/** Every `file: specifier` reached from `entry` that names a Node builtin. `read` maps a module
 *  path to its source (null when absent); relative `.js` specifiers resolve to their `.ts`. */
export function builtinsReached(entry: string, read: (file: string) => string | null): string[] {
  const hits: string[] = [];
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = read(file);
    if (text === null) continue;
    for (const m of text.matchAll(SPECIFIER)) {
      const spec = m[1]!;
      if (spec.startsWith(".")) queue.push(path.join(path.dirname(file), spec.replace(/\.js$/, ".ts")));
      else if (isBuiltin(spec)) hits.push(`${path.basename(file)}: ${spec}`);
    }
  }
  return hits.sort();
}

const fromDisk = (file: string): string | null => (fs.existsSync(file) ? fs.readFileSync(file, "utf-8") : null);

describe("the @console/shared barrel is browser-safe", () => {
  it("reaches no Node builtin from shared/src/index.ts", () => {
    expect(builtinsReached(path.join(SHARED_SRC, "index.ts"), fromDisk)).toEqual([]);
  });

  it("would fire if paths.ts were re-exported from the barrel again", () => {
    const withPaths = (file: string): string | null =>
      path.basename(file) === "index.ts" ? `export * from "./paths.js";\n${fromDisk(file) ?? ""}` : fromDisk(file);
    const hits = builtinsReached(path.join(SHARED_SRC, "index.ts"), withPaths);
    expect(hits).toContain("paths.ts: node:url");
    expect(hits).toContain("paths.ts: node:fs");
  });

  it("reads bare builtins, follows re-exports, and ignores type-only imports", () => {
    const files: Record<string, string> = {
      "/s/index.ts": `export * from "./a.js";\nexport { b } from "./b.js";\nimport type { T } from "./t.js";`,
      "/s/a.ts": `import fs from "fs";\nimport x from "some-lib";`,
      "/s/b.ts": `export const b = 1;`,
      "/s/t.ts": `import os from "node:os";`,
    };
    const read = (f: string): string | null => files[f.replace(/\\/g, "/").replace(/^[A-Za-z]:/, "")] ?? null;
    expect(builtinsReached("/s/index.ts", read)).toEqual(["a.ts: fs"]);
  });
});
