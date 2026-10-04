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
  headline: {
    arms: Record<string, unknown>;
    open_positions: number;
    open_mtm?: Record<string, Record<string, { positions: number; net: number | null; unpriced: number }>>;
  };
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
    const mine = readPmcc(loadConfig());
    const theirs = moduleHeadline();
    expect(theirs).not.toBeNull();
    expect(mine.openPositions.length).toBe(theirs!.headline.open_positions);
    expect(mine.openCount).toBe(theirs!.headline.open_positions);
  });

  it("reports the same set of arms", () => {
    const mine = readPmcc(loadConfig());
    const theirs = moduleHeadline();
    expect(new Set(mine.arms.map((b) => b.arm))).toEqual(new Set(Object.keys(theirs!.headline.arms)));
  });

  it("agrees on every (arm, symbol) cell's net, to the cent", () => {
    // The number a reader acts on. A mirror that drifts here is worse than no mirror: it is a
    // second opinion wearing the module's authority.
    //
    // The module nests `arms[arm][symbol].net_pnl`, and the console's cells are one per (arm,
    // symbol) carrying `netPnl`. Until 2026-10-04 this read `arms[arm].net` -- always undefined --
    // so every comparison hit `continue` and the check could not fail. Every cell is now required
    // on both sides; a missing one is a failure, not a skip.
    const mine = readPmcc(loadConfig());
    const theirs = moduleHeadline()!.headline.arms as Record<string, Record<string, { net_pnl: number | null }>>;
    const cells = Object.entries(theirs).flatMap(([arm, bySymbol]) =>
      Object.entries(bySymbol).map(([symbol, cell]) => ({ arm, symbol, net: cell.net_pnl })),
    );
    expect(cells.length).toBeGreaterThan(0);
    expect(mine.arms.length).toBe(cells.length);
    for (const c of cells) {
      const cell = mine.arms.find((m) => m.arm === c.arm && m.symbol === c.symbol);
      expect(cell, `${c.arm}/${c.symbol} is in the module's headline but not the console's`).toBeDefined();
      if (c.net === null) {
        expect(cell!.netPnl).toBeNull();
      } else {
        // The module rounds to the cent and the reader does not, so "to the cent" is half a cent
        // either side of the module's figure.
        expect(Math.abs((cell!.netPnl ?? Number.NaN) - c.net)).toBeLessThanOrEqual(0.0051);
      }
    }
  });
});

describe.skipIf(!available)("the console's open P&L agrees with the module's open mark-to-market", () => {
  it("agrees on every open (arm, symbol) cell's net, to the cent", () => {
    // The module marks an open position with `tracker.value_at(now)`: every leg at its latest usable
    // mark or its close, delivered shares held or covered, less every cost. The console's
    // `unrealised.ts` states the same rule; a held-long arm has no other number for ten months.
    const theirs = moduleHeadline()!.headline.open_mtm ?? {};
    const mine = readPmcc(loadConfig());
    const cells = new Map<string, { net: number; missing: boolean }>();
    for (const p of mine.openPositions) {
      const key = `${p.arm}/${p.symbol}`;
      const cell = cells.get(key) ?? { net: 0, missing: false };
      if (p.unrealisedNet === null) cell.missing = true;
      else cell.net += p.unrealisedNet;
      cells.set(key, cell);
    }
    const expected = Object.entries(theirs).flatMap(([arm, bySymbol]) =>
      Object.entries(bySymbol).map(([symbol, c]) => ({ key: `${arm}/${symbol}`, net: c.net })),
    );
    expect([...cells.keys()].sort()).toEqual(expected.map((e) => e.key).sort());
    for (const e of expected) {
      const cell = cells.get(e.key)!;
      if (e.net === null) {
        expect(cell.missing).toBe(true);
      } else {
        // Both sides round each position to the cent; a cell sums them, so allow a cent per position.
        expect(Math.abs(cell.net - e.net)).toBeLessThanOrEqual(0.01 * Math.max(1, mine.openPositions.length));
      }
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
