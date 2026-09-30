import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { peakRisk, peakRowFrom } from "../src/analytics/fliesPeakRisk.js";
import { onPeakRiskOver, peakRiskSessions } from "../src/readers/fliesPeakRisk.js";
import { flyTagScope, readModulePerformance } from "../src/readers/performance.js";
import { readFliesAnalytics } from "../src/readers/flies.js";
import { closePooledDbs } from "../src/readers/db.js";
import { resetMetricsCache, setMetricsCaller } from "../src/services/metricsBridge.js";
import { resetExcursionsCache, setExcursionsCaller } from "../src/services/excursionsBridge.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * Session peak risk: the largest open worst case a book carried at any moment. What is pinned: the
 * replay (entries, completions, a close) against hand-summed floors, including the trap that a
 * completed fly's recorded `fees` carry settlement it had not paid intraday; recorded live marks
 * preferred over the replay; an arm split by experiment stamp the way core.metrics names groups;
 * the flies performance slide carrying it per group and no other module; and the session tile's
 * figure standing after settlement.
 */

const DDL = `
CREATE TABLE fly_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT, trade_date TEXT, arm TEXT, entry_mode TEXT, symbol TEXT,
  kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL, quantity INTEGER, net REAL, credit REAL,
  debit REAL, fees REAL, floor_dollars REAL, risk_free INTEGER, entry_time TEXT, completed_at TEXT, exit_time TEXT,
  gross_pnl REAL, pnl REAL, status TEXT, experiment_id TEXT, completion_latency_min REAL);
`;

// Per-contract risk of an open 5-wide SPX short vertical at credit c: (5 - c) x 100 plus 13.44 of
// open fee and worst-case assignment reserve (13.4433) -- the live ledger's own recorded floors.
const vRisk = (c: number) => (5 - c) * 100 + 13.4433;

type Row = Record<string, unknown>;
function pos(over: Row): Row {
  return {
    position_id: `p${Math.random()}`, trade_date: "2026-09-29", arm: "control", entry_mode: "legged", symbol: "SPX",
    kind: "short_vertical", side: "put", center: 7500, wing_width: 5, far_width: null, quantity: 1, status: "settled",
    completed_at: null, exit_time: null, experiment_id: null, ...over,
  };
}

// The day, in the ledger's own terms (every row settled, so every `fees` includes settlement):
//   A 10:00 vertical 2.35, never completes                     -> 278.44 all day
//   B 10:30 vertical 2.60, completes 11:00 into a fly (+3.11)  -> 253.44 until 11:00, then 0
//   E 11:10 vertical 2.00, closed 11:20                        -> 313.44 until 11:20
//   D 11:30 vertical 2.25, never completes                     -> 288.44
// Peak: 11:10, A + E = 591.88. Priced off its settled fees, the completed fly B reads -$6.89 and
// would lift that instant to 598.77.
const DAY: Row[] = [
  pos({ position_id: "A", entry_time: "2026-09-29T10:00:00-04:00", net: 2.35, credit: 2.35, fees: 13.44, floor_dollars: -vRisk(2.35), gross_pnl: -265, pnl: -278.44 }),
  pos({
    position_id: "B", kind: "fly", entry_time: "2026-09-29T10:30:00-04:00", completed_at: "2026-09-29T11:00:00-04:00",
    net: 0.25, credit: 2.6, debit: 2.35, fees: 16.89, floor_dollars: 3.11, gross_pnl: 25, pnl: 8.11,
  }),
  pos({
    position_id: "E", status: "closed", entry_time: "2026-09-29T11:10:00-04:00", exit_time: "2026-09-29T11:20:00-04:00",
    net: 2.0, credit: 2.0, fees: 6.88, floor_dollars: -vRisk(2.0), gross_pnl: 50, pnl: 43.12,
  }),
  pos({ position_id: "D", entry_time: "2026-09-29T11:30:00-04:00", net: 2.25, credit: 2.25, fees: 13.44, floor_dollars: -vRisk(2.25), gross_pnl: 100, pnl: 86.56 }),
];

function insert(db: Database.Database, rows: Row[]) {
  for (const row of rows) {
    const cols = Object.keys(row);
    db.prepare(`INSERT INTO fly_positions (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`).run(...cols.map((c) => row[c]));
  }
}

describe("the peak-risk replay", () => {
  it("sums each position's open worst case at every entry and completion, and keeps the largest", () => {
    expect(vRisk(2.35)).toBeCloseTo(278.4433, 9);
    const out = peakRisk(DAY.map(peakRowFrom));
    expect(out.peak).toBeCloseTo(vRisk(2.35) + vRisk(2.0), 6); // 591.88
    expect(out.at).toBe("2026-09-29T11:10:00-04:00");
  });

  it("rewinds a completed fly to its open vertical before the completing leg", () => {
    const ab = peakRisk(DAY.slice(0, 2).map(peakRowFrom));
    expect(ab.peak).toBeCloseTo(vRisk(2.35) + vRisk(2.6), 6); // 531.88 at 10:30
    expect(ab.at).toBe("2026-09-29T10:30:00-04:00");
  });

  it("is zero for a book of nothing but risk-free flies", () => {
    expect(peakRisk([peakRowFrom(DAY[1]!)]).peak).toBeCloseTo(vRisk(2.6), 6); // before it completes it is a vertical
    const riskFree = { ...DAY[1]!, entry_time: "2026-09-29T11:00:00-04:00" }; // entered as its final fly
    expect(peakRisk([peakRowFrom(riskFree)])).toEqual({ peak: 0, at: null });
  });
});

describe("peak-risk sessions", () => {
  let db: Database.Database;
  beforeEach(() => {
    db = new Database(":memory:");
    db.exec(DDL);
  });
  afterEach(() => db.close());

  it("prefers the live loop's recorded peak, and replays a session that has none", () => {
    insert(db, DAY);
    insert(db, [pos({ position_id: "Y", trade_date: "2026-09-28", entry_time: "2026-09-28T10:05:00-04:00", net: 2.4, credit: 2.4, fees: 13.44, floor_dollars: -vRisk(2.4), gross_pnl: -260 })]);
    db.exec("CREATE TABLE fly_live_marks (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, position_id TEXT, open_margin REAL)");
    db.prepare("INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, open_margin) VALUES (?,?,?,?)").run("2026-09-29T11:10:30-04:00", "2026-09-29", "A", 600);
    const [s29, s28] = peakRiskSessions(db, { arm: "control" });
    expect(s29).toMatchObject({ session: "2026-09-29", peakRisk: 600, peakSource: "recorded", trades: 3, complete: true });
    expect(s29!.net).toBeCloseTo(-265 - 13.44 + 25 - 16.89 + 100 - 13.44, 6); // settled rows only: E closed
    expect(s28).toMatchObject({ session: "2026-09-28", peakSource: "replayed" });
    const all = onPeakRiskOver([s29!, s28!]);
    expect(all).toMatchObject({ sessions: 2, of: 2, replayed: 1, });
    expect(all.peakRisk).toBeCloseTo(600 + vRisk(2.4), 6);
  });

  it("splits an arm by experiment stamp, as core.metrics names its groups", () => {
    insert(db, DAY.map((r) => ({ ...r, arm: "advised:x", experiment_id: "exp-1" })));
    insert(db, [pos({ arm: "advised:x", experiment_id: "exp-2", entry_time: "2026-09-29T10:00:00-04:00", net: 1, credit: 1, fees: 13.44, floor_dollars: -vRisk(1), gross_pnl: 0 })]);
    expect(flyTagScope("advised:x@exp-1")).toEqual({ arm: "advised:x", experimentId: "exp-1" });
    expect(flyTagScope("control")).toEqual({ arm: "control", experimentId: null });
    expect(peakRiskSessions(db, { ...flyTagScope("advised:x@exp-1") })[0]!.peakRisk).toBeCloseTo(vRisk(2.35) + vRisk(2.0), 6);
    expect(peakRiskSessions(db, { ...flyTagScope("advised:x@exp-2") })[0]!.peakRisk).toBeCloseTo(vRisk(1), 6);
    expect(peakRiskSessions(db, { ...flyTagScope("advised:x") })).toEqual([]); // no unstamped rows
  });

  it("leaves a running session out of the ratio until nothing is open", () => {
    insert(db, [DAY[0]!, { ...DAY[3]!, status: "open", gross_pnl: null, pnl: null }]);
    const [s] = peakRiskSessions(db, { arm: "control" });
    expect(s).toMatchObject({ complete: false, onRisk: null });
    expect(s!.peakRisk).toBeCloseTo(vRisk(2.35) + vRisk(2.25), 6);
    expect(onPeakRiskOver([s!])).toMatchObject({ sessions: 0, of: 1, ratio: null });
  });
});

function fakeConfig(): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-peak-"));
  const data = path.join(home, "data");
  for (const m of ["flies", "curve"]) fs.mkdirSync(path.join(data, m), { recursive: true });
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(orchestratorConfig, "{}");
  return {
    port: 0,
    paths: {
      cherrypick: home, streamCacheDb: "", watchdogLast: "", orchestratorConfig, consoleData: "",
      meicDir: path.join(data, "meic"), fliesDir: path.join(data, "flies"), earningsDir: path.join(data, "earnings"),
      calendarsDir: path.join(data, "calendars"), pmccDir: path.join(data, "pmcc"), curveDir: path.join(data, "curve"),
      bwbDir: path.join(data, "bwb"), gexDir: "", reviewDir: "", overviewDir: "", advisorDir: "", adviceDir: "",
      meicRiskConfig: "", fliesConfig: "", pmccConfigCandidates: [], calendarsConfigCandidates: [], curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;
}

describe("the flies performance slide and session tile", () => {
  afterEach(() => {
    closePooledDbs();
    setMetricsCaller();
    resetMetricsCache();
    setExcursionsCaller();
    resetExcursionsCache();
  });

  it("puts return on peak risk on each flies group, and on no other module's", () => {
    const config = fakeConfig();
    const paper = new Database(path.join(config.paths.fliesDir, "paper_trades.db"));
    paper.exec(DDL);
    insert(paper, DAY);
    insert(paper, DAY.map((r) => ({ ...r, arm: "advised:x", experiment_id: "exp-1", gross_pnl: 0 })));
    paper.close();
    setExcursionsCaller(() => ({ ok: false, data: null, error: "n/a" }));
    const group = (tag: string) => [tag, { reading: { sample: 3 }, session_nets: [], trade_nets: [] }] as const;
    setMetricsCaller((_db, schema) => ({
      ok: true,
      error: null,
      metrics: { schema, n_records: 6, groups: Object.fromEntries([group("control"), group("advised:x@exp-1")]) },
    }));

    const flies = readModulePerformance(config, "flies", "ALL");
    const byTag = new Map(flies.groups.map((g) => [g.tag, g.peakRisk]));
    const net = -265 - 13.44 + 25 - 16.89 + 100 - 13.44;
    expect(byTag.get("control")).toMatchObject({ sessions: 1, of: 1, replayed: 1 });
    expect(byTag.get("control")!.ratio).toBeCloseTo(net / (vRisk(2.35) + vRisk(2.0)), 9);
    expect(byTag.get("advised:x@exp-1")!.net).toBeCloseTo(-13.44 - 16.89 - 13.44, 6);

    const curve = readModulePerformance(config, "curve", "ALL");
    expect(curve.groups.every((g) => g.peakRisk === undefined)).toBe(true);
  });

  it("holds the session's peak after its book settles", () => {
    const config = fakeConfig();
    const paper = new Database(path.join(config.paths.fliesDir, "paper_trades.db"));
    paper.exec(DDL);
    insert(paper, DAY);
    paper.close();
    const today = readFliesAnalytics(config, "paper", { arm: null, date: null, symbol: null, era: "ALL" }).today;
    expect(today.tradeDate).toBe("2026-09-29");
    expect(today.open).toBe(0); // settled: nothing open can lose any more...
    expect(today.dailyPeakRisk).toMatchObject({ at: "2026-09-29T11:10:00-04:00", source: "replayed" });
    expect(today.dailyPeakRisk!.peak).toBeCloseTo(vRisk(2.35) + vRisk(2.0), 6); // ...but the day carried this
  });
});
