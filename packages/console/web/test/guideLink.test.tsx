import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { GuideLink } from "../src/components/GuideLink";

/**
 * A help box or Help tab links to the guide section that explains it. The link must stay a string
 * literal at its call site: `tools/check_guide.py` (rule 2) reads `/help/<slug>#<anchor>` literals in
 * this source and fails when the guide has no such heading, which it cannot do for a computed href.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? sources(p) : /\.tsx?$/.test(e.name) ? [p] : [];
  });
}

describe("GuideLink", () => {
  it("renders a link to the guide section, worded as 'Read the guide'", () => {
    const html = renderToString(
      <MemoryRouter>
        <GuideLink to="/help/live#the-header" />
      </MemoryRouter>,
    );
    expect(html).toContain('href="/help/live#the-header"');
    expect(html).toContain("Read the guide →");
    expect(html).toContain('class="help-guide"');
  });

  it("takes another label for a second link beside the first", () => {
    const html = renderToString(
      <MemoryRouter>
        <GuideLink to="/help/going-live#the-halt-flag" label="Read about the halt flag →" />
      </MemoryRouter>,
    );
    expect(html).toContain('href="/help/going-live#the-halt-flag"');
    expect(html).toContain("Read about the halt flag →");
  });

  it("is used with a literal guide path everywhere, so check_guide can see it", () => {
    const uses: string[] = [];
    const bad: string[] = [];
    for (const f of sources(SRC)) {
      if (f.endsWith("GuideLink.tsx")) continue;
      const text = fs.readFileSync(f, "utf8");
      for (const m of text.matchAll(/<GuideLink\b[^>]*?\/>/gs)) {
        uses.push(f);
        if (!/\bto="\/help\/[a-z0-9-]+(?:#[A-Za-z0-9_-]+)?"/.test(m[0])) bad.push(`${path.relative(SRC, f)}: ${m[0]}`);
      }
    }
    expect(uses.length).toBeGreaterThan(20);
    expect(bad).toEqual([]);
  });
});
