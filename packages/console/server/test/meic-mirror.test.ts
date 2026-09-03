/**
 * The console's MEIC reader MIRRORS that module's analytics in TypeScript, and a mirror is only
 * safe while it is checked. Until 2026-08-26 this one could not be checked at all.
 *
 * bwb, curve and pmcc each have a test like this, comparing the page's numbers against
 * `python run.py headline` — the module's own answer, through its own query layer. MEIC had no
 * `run.py` and no CLI, so it was the one module whose reader re-implemented the most (readers/
 * meic.ts is the largest here), over the most data, on the only ledger with a live sibling, with
 * nothing on the other side of the comparison. Adding the CLI is what makes this test possible;
 * this test is why the CLI was worth adding.
 *
 * Both sides read the ledger READ-ONLY, so both see the same snapshot and a divergence is a real
 * disagreement rather than a race against the paper loop's next write.
 *
 * Scoped to `era=ALL` on both sides deliberately. The module's default is CURRENT_ERA and the
 * console's default is the same, but a mirror test that agreed only inside the current window would
 * go quiet on the day the era rolls — precisely when the two are most likely to drift.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readMeicPerformance, readMeicOpenExposure } from "../src/readers/meic.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const MEIC_PKG = path.join(REPO, "packages", "meic");
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "meic", "paper_trades.db");

interface Headline {
  ok: boolean;
  headline: {
    era: string;
    open_positions: number;
    open_capital_at_risk: number;
    arms: Record<
      string,
      { trades: number; sessions: number; gross_pnl: number | null; fees: number | null; net_pnl: number | null }
    >;
  };
}

function moduleHeadline(): Headline | null {
  if (!fs.existsSync(path.join(MEIC_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["run.py", "headline", "--era", "ALL"], {
    cwd: MEIC_PKG,
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

// Skipping is legitimate (no ledger, or no Python) but must be VISIBLE — a mirror test that skips
// silently reads as coverage. Same posture as the pmcc/bwb/curve mirrors.
if (!available) {
  // eslint-disable-next-line no-console
  console.warn("meic-mirror: skipped — no paper ledger or `python run.py headline` unavailable");
}

const consoleProfiles = () =>
  readMeicPerformance(loadConfig(), "paper", "session", null, null, "ALL").profiles;

describe.skipIf(!available)("the console's MEIC mirror agrees with the module itself", () => {
  it("reports the same set of arms", () => {
    const theirs = moduleHeadline()!.headline.arms;
    const mine = consoleProfiles();
    expect(new Set(mine.map((p) => p.profile))).toEqual(new Set(Object.keys(theirs)));
  });

  it("reports the same trade and session count per arm", () => {
    const theirs = moduleHeadline()!.headline.arms;
    for (const row of consoleProfiles()) {
      expect.soft(row.trades, `${row.profile} trades`).toBe(theirs[row.profile]?.trades);
      expect.soft(row.sessions, `${row.profile} sessions`).toBe(theirs[row.profile]?.sessions);
    }
  });

  it("reports the same net per arm, to the cent", () => {
    // The subtraction itself is the thing worth pinning. MEIC stores `pnl` GROSS and `fees`
    // separately, so "net" is a convention applied at read time on both sides — and readers/meic.ts
    // already carries a comment about two of its own surfaces reading gross where the calendar
    // beside them read net. One of those is exactly how a mirror goes quietly wrong.
    const theirs = moduleHeadline()!.headline.arms;
    for (const row of consoleProfiles()) {
      expect.soft(row.netPnl, `${row.profile} net`).toBeCloseTo(theirs[row.profile]?.net_pnl ?? NaN, 2);
      expect.soft(row.grossPnl, `${row.profile} gross`).toBeCloseTo(theirs[row.profile]?.gross_pnl ?? NaN, 2);
      expect.soft(row.fees, `${row.profile} fees`).toBeCloseTo(theirs[row.profile]?.fees ?? NaN, 2);
    }
  });

  it("agrees that net is gross minus fees on both sides", () => {
    // Guards the case where both sides are internally consistent and both wrong in the same way:
    // if either started reporting gross AS net, the comparisons above would still pass.
    for (const row of consoleProfiles()) {
      expect.soft(row.netPnl, `${row.profile}`).toBeCloseTo(row.grossPnl - row.fees, 2);
    }
  });

  it("reports the same open count and open capital at risk as the module's own headline", () => {
    // Not era-scoped on either side -- headline's open_positions/open_capital_at_risk queries
    // carry no era filter in analytics.py, and readMeicOpenExposure mirrors that.
    const theirs = moduleHeadline()!.headline;
    const mine = readMeicOpenExposure(loadConfig(), "paper");
    expect(mine.open).toBe(theirs.open_positions);
    expect(mine.capitalAtRisk).toBeCloseTo(theirs.open_capital_at_risk, 2);
  });
});
