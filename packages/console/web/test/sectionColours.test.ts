import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Each section colour is set once. The redesign's page PRs each appended a block to styles.css, and a
 * later block's `.pf-c-hist` silently repainted every record section in the suite (2026-11-07). The
 * concepts come from Section.tsx, so a new one is covered without a list to keep.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const css = fs.readFileSync(path.join(SRC, "styles.css"), "utf8");
const section = fs.readFileSync(path.join(SRC, "components", "Section.tsx"), "utf8");

export function concepts(tsx: string): string[] {
  const m = /export type Concept = ([^;]+);/.exec(tsx);
  return m ? [...m[1]!.matchAll(/"([a-z]+)"/g)].map((x) => x[1]!) : [];
}

export function definitions(styles: string, concept: string): number {
  return [...styles.matchAll(new RegExp(`^\\.pf-c-${concept} \\{`, "gm"))].length;
}

describe("section colours", () => {
  it("reads the concepts from the one Section", () => {
    expect(concepts(section)).toEqual(expect.arrayContaining(["stand", "hist", "edge", "live"]));
  });

  it("sets each concept's colour exactly once", () => {
    for (const c of concepts(section)) expect([c, definitions(css, c)]).toEqual([c, 1]);
  });

  it("catches a second definition", () => {
    expect(definitions(css + "\n.pf-c-hist { --pf-c: red; }\n", "hist")).toBe(2);
  });
});
