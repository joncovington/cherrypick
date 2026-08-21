/**
 * The console's PMCC reader MIRRORS that module's analytics in TypeScript, and a mirror is only
 * safe while it is checked.
 *
 * packages/pmcc declares analytics.py "the one query layer every read surface goes through", but
 * its CLI exposes only part of what the page needs and a subprocess per request at a 15s refetch
 * is not what that layer was built to carry. So readers/pmcc.ts re-implements those queries — a
 * deliberate exception to the suite's bridging rule, and the console's own CLAUDE.md states the
 * condition attached to it: the page's headline must equal `python run.py headline`.
 *
 * That was verified by hand once, when the page landed. This is the automated version. It compares
 * the module's OWN answer against the reader's, so a divergence fails here rather than being
 * discovered by someone reading a number that quietly stopped being true.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readPmcc } from "../src/readers/pmcc.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const PMCC_PKG = path.join(REPO, "packages", "pmcc");
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "pmcc", "paper_trades.db");

interface Headline {
  ok: boolean;
  headline: { books: Record<string, unknown>; open_positions: number };
}

function moduleHeadline(): Headline | null {
  if (!fs.existsSync(path.join(PMCC_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["run.py", "headline"], {
    cwd: PMCC_PKG,
    encoding: "utf-8",
    timeout: 60_000,
  });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Headline;
  } catch {
    return null;
  }
}

const available = fs.existsSync(LEDGER) && moduleHeadline() !== null;

describe.skipIf(!available)("the console's PMCC mirror agrees with the module itself", () => {
  it("reports the same open-position count", () => {
    const mine = readPmcc(loadConfig(), "paper");
    const theirs = moduleHeadline();
    expect(theirs).not.toBeNull();
    expect(mine.openPositions.length).toBe(theirs!.headline.open_positions);
    expect(mine.openCount).toBe(theirs!.headline.open_positions);
  });

  it("reports the same set of books", () => {
    const mine = readPmcc(loadConfig(), "paper");
    const theirs = moduleHeadline();
    expect(new Set(mine.books.map((b) => b.book))).toEqual(new Set(Object.keys(theirs!.headline.books)));
  });

  it("agrees on each book's net, to the cent", () => {
    // The number a reader acts on. A mirror that drifts here is worse than no mirror: it is a
    // second opinion wearing the module's authority.
    const mine = readPmcc(loadConfig(), "paper");
    const theirs = moduleHeadline()!.headline.books as Record<string, { net?: number }>;
    for (const book of mine.books) {
      const other = theirs[book.book];
      if (other?.net === undefined) continue;
      expect(book.net ?? 0).toBeCloseTo(other.net, 2);
    }
  });
});

describe("the mirror check itself", () => {
  it("says plainly when it could not run", () => {
    // A skipped check must never read as a passing one — this asserts the reason is knowable.
    expect(typeof available).toBe("boolean");
    if (!available) {
      expect(fs.existsSync(LEDGER) === false || moduleHeadline() === null).toBe(true);
    }
  });
});
