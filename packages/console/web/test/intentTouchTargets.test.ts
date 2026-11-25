import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The intent surfaces (the position's strip, the Intent tab, the PMCC row, the needs-a-look and no-intent
 * links, Config › Intents) are used by a finger on a tablet or a phone, so every control in them is 44px
 * there (design review). The rule is one block in styles.css; these pin what it covers, so a control added
 * without a place in it, or a selector dropped from it, is caught. The rendered sizes were measured in a
 * browser at 390 and 768 (see the PR); this holds the CSS that produced them.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const css = fs.readFileSync(path.join(SRC, "styles.css"), "utf8").replace(/\r\n/g, "\n");

/** The body of the first rule block whose header matches `header`, braces balanced. */
function block(header: RegExp): string {
  const m = header.exec(css);
  expect(m, `no ${header} in styles.css`).not.toBeNull();
  const open = css.indexOf("{", m!.index);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`unbalanced ${header}`);
}

const touch = block(/@media \(max-width: 820px\), \(pointer: coarse\)\s*\{/);
const phone = block(/@media \(max-width: 560px\)\s*\{\s*\.int-strip-opts/);
const decl = (body: string, prop: string, value: string) => new RegExp(`${prop}:\\s*${value}\\s*;`).test(body);

describe("the intent surfaces' touch targets", () => {
  it("make every button, chip, field and select 44px on a tablet or phone", () => {
    const rule = /([^{}]+)\{\s*min-height:\s*44px\s*;\s*\}/.exec(touch);
    expect(rule, "no min-height: 44px rule").not.toBeNull();
    const selectors = rule![1]!.split(",").map((s) => s.trim());
    for (const s of [
      ".int-strip-opt",
      ".int-col .btn",
      ".int-tab .btn",
      ".pmcc-intent .btn",
      ".int-strip .btn",
      ".int-chip:not(.is-static)",
      ".cfg-intents .btn",
      ".cfg-intents input",
      ".cfg-intents select",
    ]) {
      expect(selectors, s).toContain(s);
    }
  });

  it("gives a link inside a sentence a 44px hit area above and below it, never sideways, and no taller line", () => {
    for (const surface of [".int-strip", ".int-tab", ".pmcc-intent", ".cfg-intents"]) {
      expect(touch, surface).toContain(`${surface} a.lk-sub::after`);
    }
    expect(touch).toMatch(/inset:\s*-15px 0\s*;/);
    expect(touch).not.toMatch(/\ba(\.lk-sub)?\s*\{[^}]*display:\s*inline-flex/);
  });

  it("stacks the answers at full width on a phone", () => {
    expect(decl(phone, "flex-direction", "column")).toBe(true);
    expect(decl(phone, "width", "100%")).toBe(true);
    expect(phone).toMatch(/\.int-strip-opt\s*\{[^}]*width:\s*100%/);
  });

  it("is not applied on a desktop with a mouse: the 44px rule lives only under the touch media query", () => {
    const outside = css.replace(touch, "");
    for (const s of [".int-strip-opt", ".int-tab .btn", ".pmcc-intent .btn", ".cfg-intents .btn"]) {
      const escaped = s.replace(/[.\\^$*+?()[\]{}|]/g, "\\$&");
      expect(outside, s).not.toMatch(new RegExp(`${escaped}(?![\\w-])[^{}]*\\{[^}]*min-height:\\s*44px`));
    }
  });

  it("reads the verdict under an answer as an outcome, empty to a screen reader, and lines up the Intent tab's buttons", () => {
    expect(css).toMatch(/\.int-strip-verdict::before\s*\{[^}]*content:\s*"→ " \/ ""/);
    expect(css).toMatch(/\.int-tab \.int-col > \.btn\s*\{\s*margin-top:\s*auto;/);
  });

  it("is hung on a class the Config card carries", () => {
    const card = fs.readFileSync(path.join(SRC, "pages", "Config", "IntentsCard.tsx"), "utf8");
    expect(card).toMatch(/className="[^"]*\bcfg-intents\b/);
  });
});
