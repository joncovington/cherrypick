import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * A `.cards` modifier is written WITH the base, never alone.
 *
 * `.cards` owns the grid — `display: grid` and the 0.75rem gap — and `.cards-pairs` / `.cards-wide`
 * set only the track shape. Written alone (`className="cards-pairs"`) the container is not a grid at
 * all: the cards stack with no gap and the intended columns never happen. Four call sites had drifted
 * that way (Flow's trades / vol-OI / birdseye and PMCC's tracker), invisible because both pages are
 * off — the layout only breaks when the page is turned on.
 *
 * The modifier names come from the stylesheet, so a new `.cards-*` modifier is covered without a list
 * to keep. The `.cards` base itself is the token `cards`.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

const CLASSNAME = /className="([^"]*)"|className=\{"([^"]*)"\}|className=\{`([^`$]*)`\}/g;

export function cardModifiers(css: string): string[] {
  return [...new Set([...css.matchAll(/\.(cards-[a-zA-Z][\w-]*)/g)].map((m) => m[1]!))];
}

export function baseStrays(
  files: Array<{ name: string; text: string }>,
  modifiers: string[],
): string[] {
  const hits: string[] = [];
  for (const f of files) {
    f.text.split("\n").forEach((line, i) => {
      for (const m of line.matchAll(CLASSNAME)) {
        const tokens = (m[1] ?? m[2] ?? m[3] ?? "").split(/\s+/).filter(Boolean);
        if (tokens.includes("cards")) continue;
        const stray = tokens.find((t) => modifiers.includes(t));
        if (stray) hits.push(`${f.name}:${i + 1} .${stray}`);
      }
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

describe("a .cards modifier never stands alone", () => {
  const files = sources(SRC);
  const css = files.find((f) => f.name.endsWith("styles.css"));
  const modifiers = css ? cardModifiers(css.text) : [];

  it("reads the modifiers the stylesheet declares", () => {
    expect(modifiers).toEqual(expect.arrayContaining(["cards-pairs", "cards-wide"]));
  });

  it("no element writes one without the cards base", () => {
    expect(baseStrays(files, modifiers)).toEqual([]);
  });

  it("fires on a modifier alone, and passes one with the base", () => {
    const m = ["cards-pairs", "cards-wide"];
    expect(baseStrays([{ name: "x.tsx", text: '<div className="cards-pairs">' }], m)).toEqual(["x.tsx:1 .cards-pairs"]);
    expect(baseStrays([{ name: "x.tsx", text: '<div className={"cards-wide"} />' }], m)).toEqual(["x.tsx:1 .cards-wide"]);
    expect(baseStrays([{ name: "x.tsx", text: '<div className="cards cards-pairs view-fade">' }], m)).toEqual([]);
  });
});
