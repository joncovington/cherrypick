import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The adopted theme (2026-10): every sign colour reads `--pos`/`--neg` (or `tokenAlpha` on a
 * canvas), so it follows the theme. The pre-adoption palette's green, red and cherry literals
 * never followed the light theme; this fails if one comes back anywhere in web/src.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const OLD = /rgba\(\s*67,\s*181,\s*122|rgba\(\s*217,\s*92,\s*74|rgba\(\s*210,\s*63,\s*87|#43b57a|#d95b5b|#d95c4a/i;

export function oldPaletteHits(files: Array<{ name: string; text: string }>): string[] {
  const hits: string[] = [];
  for (const f of files) {
    f.text.split("\n").forEach((line, i) => {
      // A token's own definition (`--live: #43b57a;`) and a canvas read's fallback behind the
      // token (`hexOf("--neg", "#d95b5b")`) are the palette working, not a bypass of it.
      if (/^\s*--[\w-]+\s*:/.test(line) || /\b(hexOf|getHex|tokenAlpha)\(/.test(line)) return;
      if (OLD.test(line)) hits.push(`${f.name}:${i + 1}`);
    });
  }
  return hits;
}

function sources(dir: string): Array<{ name: string; text: string }> {
  const out: Array<{ name: string; text: string }> = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...sources(p));
    else if (/\.(tsx?|css)$/.test(e.name)) out.push({ name: path.relative(SRC, p), text: fs.readFileSync(p, "utf8") });
  }
  return out;
}

describe("the old palette stays gone", () => {
  it("no pre-adoption sign colour is painted anywhere in web/src", () => {
    expect(oldPaletteHits(sources(SRC))).toEqual([]);
  });

  it("the guard fires on a painted literal, and not on a token definition or a token read's fallback", () => {
    expect(oldPaletteHits([{ name: "x.tsx", text: 'const up = "rgba(67, 181, 122, 0.3)";' }])).toEqual(["x.tsx:1"]);
    expect(oldPaletteHits([{ name: "s.css", text: "  --live: #43b57a;" }])).toEqual([]);
    expect(oldPaletteHits([{ name: "y.tsx", text: 'const C = hexOf("--neg", "#d95b5b");' }])).toEqual([]);
  });
});
