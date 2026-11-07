import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The suite sorts its own trades on size, timing and fit, never on credit, yield or theta per buying
 * power: none has a tested edge, and a sort on one reads as a ranking by it. pmcc's open-trades table
 * still offered "weekly yield" as a sort until 2026-11-07. This reads every sortable header
 * (`SortTh` / `FlowTh` keys) and every `*_SORTS` list in the web source and refuses those keys.
 *
 * Options flow's "Premium" is the dollar size of someone else's trade, a size, so it is not refused.
 */
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const REFUSED = /^(yield|weeklyYield|credit|netCredit|theta|thetaPerBp|tbp|ror)$/i;

function files(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? files(p) : /\.tsx?$/.test(e.name) ? [p] : [];
  });
}

export function sortKeys(text: string): string[] {
  const keys = [...text.matchAll(/<(?:SortTh|FlowTh)\b[^>]*\bk="([^"]+)"/g)].map((m) => m[1]!);
  for (const list of text.matchAll(/_SORTS\b[^=]*=\s*\[([\s\S]*?)\];/g)) {
    keys.push(...[...list[1]!.matchAll(/\bid:\s*"([^"]+)"/g)].map((m) => m[1]!));
  }
  return keys;
}

describe("sorts", () => {
  it("never rank the suite's own trades by credit, yield or theta", () => {
    const hits = files(SRC).flatMap((f) =>
      sortKeys(fs.readFileSync(f, "utf8"))
        .filter((k) => REFUSED.test(k))
        .map((k) => `${path.relative(SRC, f)}: ${k}`),
    );
    expect(hits).toEqual([]);
  });

  it("would catch one", () => {
    expect(sortKeys('<SortTh label="weekly yield" k="yield" sort={sort} />')).toEqual(["yield"]);
    expect(sortKeys('export const X_SORTS = [{ id: "time" }, { id: "credit" }];').filter((k) => REFUSED.test(k))).toEqual(["credit"]);
  });
});
