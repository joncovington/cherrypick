/**
 * The summary slide's one read, checked against the ledger it reads — the reconciliation identity
 * the slide's design promises: the hero's money is the SUM OF THE ARMS' rows, to the cent, over
 * every window; and the hero itself agrees with `readMeic`'s own totals for the same window (the
 * shape the history table shows), because two readers answering one question differently is the
 * failure this endpoint exists to prevent.
 *
 * Skipped when no paper ledger exists locally, like every mirror test in this suite.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readMeic, readMeicSummary } from "../src/readers/meic.js";

const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "meic", "paper_trades.db");
const available = fs.existsSync(LEDGER);

describe.skipIf(!available)("readMeicSummary reconciles", () => {
  const config = loadConfig();

  it("hero equals the sum of the arms, over every window", () => {
    for (const range of ["day", "week", "month", "year", "all"] as const) {
      const s = readMeicSummary(config, "paper", { symbol: null, profile: null, era: null }, range, null);
      const armSum = s.arms.reduce((sum, a) => sum + a.net, 0);
      expect(Math.round(armSum * 100)).toBe(Math.round(s.hero.net * 100));
    }
  });

  it("hero agrees with readMeic's totals for the same day window", () => {
    const s = readMeicSummary(config, "paper", { symbol: null, profile: null, era: null }, "day", null);
    if (s.window.from === null) return; // no resolved session — nothing to compare
    const m = readMeic(config, "paper", {
      ...{ symbol: null, profile: null, era: null },
      day: null,
      from: s.window.from,
      to: s.window.to,
      outcome: "all",
      view: "history",
      reason: null,
      search: "",
      limit: 1,
      offset: 0,
    });
    expect(s.hero.net).toBe(m.totals.net);
    expect(s.hero.gross).toBe(m.totals.gross);
    expect(s.hero.fees).toBe(m.totals.fees);
    expect(s.hero.settlement).toBe(m.totals.settlementFees);
    expect(s.hero.trades).toBe(m.totals.trades);
  });

  it("the refusal ledger's parts account for every evaluation", () => {
    const s = readMeicSummary(config, "paper", { symbol: null, profile: null, era: null }, "all", null);
    const outcomeSum = s.evaluations.outcomes.reduce((sum, o) => sum + o.n, 0);
    expect(outcomeSum).toBe(s.evaluations.refused);
    expect(s.evaluations.filled + s.evaluations.refused).toBe(s.evaluations.evaluated);
  });
});
