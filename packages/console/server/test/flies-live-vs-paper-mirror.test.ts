/**
 * The flies Performance and Live pages show the pilot's abort rule -- halt when live completion
 * runs more than ABORT_COMPLETION_GAP below contemporaneous paper over at least
 * ABORT_MIN_LIVE_ENTRIES live entries -- and the console computes it in TypeScript
 * (readers/flies.ts `abortVerdict`). The rule's home is cherrypick.flies.analytics.live_vs_paper,
 * which eod.py and the live loop report from. A copied rule is only safe while it is checked.
 *
 * Two suites. The first pins the constants and the boundary cases and runs unconditionally; it is
 * the guard shown to fail (flip either constant and it reports). The second asks the Python itself:
 * every fixture case is written as fly_positions rows into an in-memory SQLite pair, run through
 * `live_vs_paper`, and compared constant-for-constant and verdict-for-verdict against the TS rule on
 * the same counts. It skips cleanly and visibly when Python or the flies package is unavailable.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { ABORT_COMPLETION_GAP, ABORT_MIN_LIVE_ENTRIES, abortVerdict, completionRate } from "../src/readers/flies.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const FLIES_PKG = path.join(REPO, "packages", "flies");

interface Side {
  entries: number;
  completed: number;
}
interface Case {
  live: Side;
  paper: Side;
}

describe("the console's abort rule, pinned", () => {
  it("carries the flies module's constants", () => {
    expect(ABORT_MIN_LIVE_ENTRIES).toBe(30);
    expect(ABORT_COMPLETION_GAP).toBe(0.15);
  });

  it("compares the gap rounded to four places, as live_vs_paper does", () => {
    // 0.65 - 0.50 is 0.15000000000000002 in floating point: unrounded, this would fire.
    const v = abortVerdict({ entries: 30, completed: 15 }, { entries: 40, completed: 26 });
    expect(v.completionGap).toBe(0.15);
    expect(v.armed).toBe(true);
    expect(v.triggered).toBe(false);
    expect(abortVerdict({ entries: 30, completed: 15 }, { entries: 40, completed: 27 }).triggered).toBe(true);
  });

  it("is not armed below the live-entry minimum, however wide the gap", () => {
    const v = abortVerdict({ entries: 29, completed: 0 }, { entries: 29, completed: 29 });
    expect(v.armed).toBe(false);
    expect(v.triggered).toBe(false);
  });

  it("has no gap and no verdict without a paper side", () => {
    expect(abortVerdict({ entries: 40, completed: 0 }, null)).toEqual({ completionGap: null, armed: true, triggered: false });
    expect(abortVerdict({ entries: 40, completed: 0 }, { entries: 0, completed: 0 }).completionGap).toBeNull();
    expect(completionRate(1, 3)).toBe(0.3333);
  });
});

// --------------------------------------------------------------------------- against the Python

/** Hand-picked boundaries, then a seeded spread so the comparison is not only the cases chosen. */
function cases(): Case[] {
  const out: Case[] = [
    { live: { entries: 30, completed: 15 }, paper: { entries: 40, completed: 26 } },
    { live: { entries: 30, completed: 15 }, paper: { entries: 40, completed: 27 } },
    { live: { entries: 29, completed: 0 }, paper: { entries: 29, completed: 29 } },
    { live: { entries: 30, completed: 0 }, paper: { entries: 0, completed: 0 } },
    { live: { entries: 3, completed: 1 }, paper: { entries: 3, completed: 2 } },
    { live: { entries: 100, completed: 50 }, paper: { entries: 20, completed: 13 } },
    { live: { entries: 60, completed: 45 }, paper: { entries: 60, completed: 30 } },
  ];
  let seed = 20261012;
  const next = (n: number) => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed % n;
  };
  for (let i = 0; i < 60; i += 1) {
    const le = 1 + next(70);
    const pe = next(70);
    out.push({ live: { entries: le, completed: next(le + 1) }, paper: { entries: pe, completed: next(pe + 1) } });
  }
  return out;
}

const SCRIPT = [
  "import json, sqlite3, sys",
  "from cherrypick.flies import analytics",
  "DDL = ('CREATE TABLE fly_positions (trade_date TEXT, arm TEXT, entry_mode TEXT, status TEXT, kind TEXT, '",
  "       'credit REAL, debit REAL, completion_latency_min REAL, void_reason TEXT)')",
  "ROW = 'INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?,?,?)'",
  "def ledger(side, live):",
  "    conn = sqlite3.connect(':memory:')",
  "    conn.row_factory = sqlite3.Row",
  "    conn.execute(DDL)",
  "    for i in range(side['entries']):",
  "        kind = 'fly' if i < side['completed'] else 'short_vertical'",
  "        conn.execute(ROW, ('2026-10-01', 'control', 'legged', 'settled', kind, 1.0, 0.5, 10.0, None))",
  "    # Voided rows (still settled, void_reason alone marks them) that the counts above leave out:",
  "    # completed on live and failed on paper, so counting them moves the gap; and a live session",
  "    # whose only row is void, beside a paper row that session would otherwise pull in.",
  "    kind = 'fly' if live else 'short_vertical'",
  "    for _ in range(3):",
  "        conn.execute(ROW, ('2026-10-01', 'control', 'legged', 'settled', kind, 1.0, 0.5, 10.0, 'outage'))",
  "    conn.execute(ROW, ('2026-10-08', 'control', 'legged', 'settled', 'short_vertical', 1.0, None, None,",
  "                       'outage' if live else None))",
  "    return conn",
  "out = {'min_live_entries': analytics.ABORT_MIN_LIVE_ENTRIES, 'gap_limit': analytics.ABORT_COMPLETION_GAP, 'cases': []}",
  "for c in json.load(sys.stdin):",
  "    r = analytics.live_vs_paper(ledger(c['live'], True), ledger(c['paper'], False), 'control')",
  "    out['cases'].append({'gap': r['completion_gap'], 'armed': r['abort_rule']['armed'],",
  "                         'triggered': r['abort_rule']['triggered'], 'min_live_entries': r['abort_rule']['min_live_entries'],",
  "                         'gap_limit': r['abort_rule']['gap_limit']})",
  "print(json.dumps(out))",
].join("\n");

interface Theirs {
  min_live_entries: number;
  gap_limit: number;
  cases: Array<{ gap: number | null; armed: boolean; triggered: boolean; min_live_entries: number; gap_limit: number }>;
}

function askModule(input: Case[]): Theirs | null {
  if (!fs.existsSync(path.join(FLIES_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["-c", SCRIPT], {
    cwd: FLIES_PKG,
    encoding: "utf-8",
    timeout: 60_000,
    input: JSON.stringify(input),
  });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Theirs;
  } catch {
    return null;
  }
}

const fixtures = cases();
const theirs = askModule(fixtures);

describe.skipIf(theirs === null)("the console's abort rule agrees with cherrypick.flies.analytics.live_vs_paper", () => {
  it("on the constants", () => {
    expect(ABORT_MIN_LIVE_ENTRIES).toBe(theirs!.min_live_entries);
    expect(ABORT_COMPLETION_GAP).toBe(theirs!.gap_limit);
  });

  it("on the gap, the arming and the verdict, case for case", () => {
    expect(theirs!.cases).toHaveLength(fixtures.length);
    let triggered = 0;
    fixtures.forEach((c, i) => {
      const mine = abortVerdict(c.live, c.paper);
      const other = theirs!.cases[i]!;
      const label = `case ${i}: live ${c.live.completed}/${c.live.entries}, paper ${c.paper.completed}/${c.paper.entries}`;
      expect(mine.completionGap, label).toBe(other.gap);
      expect(mine.armed, label).toBe(other.armed);
      expect(mine.triggered, label).toBe(other.triggered);
      if (other.triggered) triggered += 1;
    });
    // The comparison is only worth something if it saw the rule fire as well as hold.
    expect(triggered).toBeGreaterThan(0);
    expect(triggered).toBeLessThan(fixtures.length);
  });
});

describe("the mirror check itself", () => {
  it("says plainly when it could not run", () => {
    if (theirs === null) {
      console.warn("flies live-vs-paper mirror SKIPPED: python or cherrypick.flies unavailable");
    }
    expect(theirs === null || theirs.cases.length === fixtures.length).toBe(true);
  });
});
