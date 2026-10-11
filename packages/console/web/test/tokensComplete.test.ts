/**
 * The token-completeness guard: every surface below the `:root` blocks derives from the tokens,
 * so the light theme (and any later one) re-specifies one block instead of hunting hexes.
 *
 * This exists because the sweep it guards was verified to fail: the advisor cells, the
 * expected-move band, the skeleton shimmer and one undefined-token fallback (`--surface-2`)
 * all survived the earlier passes as literals, and a light boot would have painted them dark.
 * The guard reads the stylesheet the way the browser does — the `:root` blocks first, then
 * everything after — and lets exactly two literals past: white ink on saturated-accent fills
 * (`.ts-seg-on`, `.lock-btn-halt`), where the accent behind the ink is itself a token in both
 * themes and the ink must not invert with the theme.
 */
import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

describe("token completeness", () => {
  it("names no colour outside the :root token blocks, bar ink-on-accent", () => {
    const cut = css.indexOf("color-scheme: light;") + "color-scheme: light;".length;
    expect(cut).toBeGreaterThan(0); // the light block must exist for the cut to mean anything
    const rest = css.slice(cut);
    const hexes = rest.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
    const allowed = new Set(["#fff", "#ffffff"]);
    const stray = hexes.filter((h) => !allowed.has(h.toLowerCase()));
    expect(stray, `stray literals below the token blocks: ${[...new Set(stray)].join(", ")}`).toEqual([]);
  });

  it("keeps the two themes in step: the light block re-specifies every dark token", () => {
    const darkBlock = css.slice(css.indexOf(":root {"), css.indexOf("color-scheme: dark;"));
    const lightBlock = css.slice(css.indexOf(':root[data-theme="light"]'), css.indexOf("color-scheme: light;"));
    const names = (block: string) =>
      [...block.matchAll(/^\s*--[a-z0-9-]+:/gm)].map((m) => m[0].trim().replace(/:$/, "")).filter((n) => n !== "--num-face");
    const dark = new Set(names(darkBlock));
    const light = new Set(names(lightBlock));
    const missing = [...dark].filter((n) => !light.has(n));
    expect(missing, `the light block is missing tokens the dark block declares: ${missing.join(", ")}`).toEqual([]);
    const extra = [...light].filter((n) => !dark.has(n));
    expect(extra, `the light block declares tokens the dark block does not: ${extra.join(", ")}`).toEqual([]);
  });

  it("keeps the stylesheet's tokens and the canvas palette's reads in step", () => {
    const ts = readFileSync(new URL("../src/components/chart/cssvars.ts", import.meta.url), "utf8");
    const reads = [...ts.matchAll(/getHex\("(--[a-z0-9-]+)"/g)].map((m) => m[1]);
    expect(reads.length, "cssvars.ts must read tokens through getHex").toBeGreaterThan(0);
    for (const name of reads) {
      expect(css.includes(`${name}:`), `cssvars.ts reads ${name}, which styles.css does not declare`).toBe(true);
    }
  });
});
