/**
 * The console's profit forest is a TypeScript MIRROR of cherrypick.flies' fly.py payoff core
 * (analytics/fliesPayoff.ts), and a mirror is only safe while it is checked -- the
 * meic/bwb/curve/pmcc-mirror precedent. This one earned its place the hard way: the port drew a
 * debit_first long vertical as the mirror of a short vertical (ramping from the centre outward)
 * where fly.py prices it with debit_vertical_payoff (ramping from the far strike INTO the centre),
 * a full wing width apart, and nothing noticed for a month because no test touched the port.
 *
 * Two suites. The first ports fly.py's own hand-computed fixtures (tests/test_fly_math.py) and
 * runs unconditionally; it is the guard shown to fail. The second reads the real paper ledger and
 * asks fly.py itself for every position's P&L on a price grid plus its floor, comparing to the
 * cent; it skips cleanly and visibly when the ledger or the module is absent.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";

import {
  bookFloor,
  bookPnl,
  flyPayoff,
  positionFloor,
  positionPnl,
  scanPrices,
  verticalOpenFee,
  type FlyPosition,
} from "../src/analytics/fliesPayoff.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const FLIES_PKG = path.join(REPO, "packages", "flies");
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "flies", "paper_trades.db");

const ASSIGNMENT_FEE = 5;

function pos(over: Partial<FlyPosition>): FlyPosition {
  return {
    kind: "fly",
    side: "put",
    center: 6000,
    wingWidth: 5,
    farWidth: null,
    net: 0,
    quantity: 1,
    fees: 0,
    status: "open",
    ...over,
  };
}

/** A position's per-contract expiry payoff, backed out of positionPnl with net=0, fees=0, status=settled. */
function payoffOf(p: FlyPosition, s: number): number {
  return positionPnl({ ...p, net: 0, fees: 0, status: "settled" }, s) / 100;
}

describe("fliesPayoff mirrors fly.py's hand-computed fixtures", () => {
  it("fly payoff peaks at the centre and lives in [0, W]", () => {
    expect(flyPayoff(6000, 5, 6000)).toBe(5);
    expect(flyPayoff(6000, 5, 6005)).toBe(0);
    expect(flyPayoff(6000, 5, 5995)).toBe(0);
    expect(flyPayoff(6000, 5, 6002.5)).toBe(2.5);
    for (const off of [-100, -10, -5, -2.5, 0, 2.5, 5, 10, 100]) {
      const v = flyPayoff(6000, 5, 6000 + off);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(5);
    }
  });

  it("short vertical payoff is bounded and never positive", () => {
    const sv = (side: string, s: number) => payoffOf(pos({ kind: "short_vertical", side }), s);
    expect(sv("put", 6100)).toBe(0);
    expect(sv("put", 5990)).toBe(-5);
    expect(sv("call", 5900)).toBe(0);
    expect(sv("call", 6010)).toBe(-5);
    expect(sv("put", 5998)).toBe(-2);
  });

  it("long vertical (debit_first's opening trade) ramps between the FAR strike and the centre, call side", () => {
    // +1 5995 call / -1 6000 call: worthless at/below 5995, maxed at/above 6000.
    const lv = (s: number) => payoffOf(pos({ kind: "long_vertical", side: "call" }), s);
    expect(lv(5990)).toBe(0);
    expect(lv(5995)).toBe(0);
    expect(lv(6000)).toBe(5);
    expect(lv(6010)).toBe(5);
    expect(lv(5997.5)).toBe(2.5);
  });

  it("long vertical put side mirrors the call side", () => {
    // +1 6005 put / -1 6000 put: worthless at/above 6005, maxed at/below 6000.
    const lv = (s: number) => payoffOf(pos({ kind: "long_vertical", side: "put" }), s);
    expect(lv(6010)).toBe(0);
    expect(lv(6005)).toBe(0);
    expect(lv(6000)).toBe(5);
    expect(lv(5990)).toBe(5);
    expect(lv(6002.5)).toBe(2.5);
  });

  it("long vertical P&L at the centre is the full width less the debit, less one settlement event", () => {
    // fly.position_pnl on the same dict: (-1 + 5) * 100 - $5 for the one ITM strike = 395.
    const p = pos({ kind: "long_vertical", side: "call", net: -1 });
    expect(positionPnl(p, 6000)).toBe(395);
    expect(positionPnl(p, 5995)).toBe(-100);
    expect(positionPnl(p, 6005)).toBe(390);
  });

  it("long vertical floor is bounded at zero, reserving ONE settlement event not two", () => {
    const p = pos({ kind: "long_vertical", side: "call", net: -1, fees: 0 });
    expect(positionFloor(p)).toBeCloseTo(-100 - ASSIGNMENT_FEE, 6);
    expect(positionFloor(pos({ kind: "long_vertical", side: "call", net: 0 }))).toBeCloseTo(-ASSIGNMENT_FEE, 6);
  });

  it("charges the assignment fee per distinct ITM strike, never scaled by quantity", () => {
    // A 2-lot fly deep ITM on all three strikes: 3 events, $15, not $30.
    const p = pos({ kind: "fly", side: "put", net: 1, quantity: 2 });
    expect(positionPnl(p, 5900)).toBe(1 * 100 * 2 - 3 * ASSIGNMENT_FEE);
  });

  it("scan grid carries a point one cent either side of every strike, padded a strike span", () => {
    const grid = scanPrices([pos({ kind: "fly", center: 6000, wingWidth: 5 })], 1);
    for (const s of [5995, 6000, 6005]) {
      expect(grid).toContain(s - 0.01);
      expect(grid).toContain(s + 0.01);
    }
    expect(grid[0]).toBe(5985);
    expect(grid[grid.length - 1]).toBe(6015);
  });

  it("floor sees the assignment-fee step just past a strike that the display grid cannot", () => {
    // An OPEN short put spread, short 6000 / long 5995, credit 2.00: payoff is -5 below 5995, so
    // the dollar worst is -300 -- and one cent below 5995 BOTH strikes are ITM, two $5 events.
    const p = pos({ kind: "short_vertical", side: "put", net: 2, status: "open" });
    const f = bookFloor([p], 1);
    expect(f.worst).toBe(-310);
    expect(f.worstTail).toBe("below");
    expect(f.worstAt).toBe(5994.99);
    expect(f.floorHolds).toBe(false);
    expect(f.unboundedBelow).toBe(true);
    expect(f.locked).toBe(false);
  });

  it("names the inner end of a flat tail and which way it runs", () => {
    // A settled call fly at 6000 with credit 0.25: flat at +25 below 5995 and above 6005, peak 525.
    const f = bookFloor([pos({ kind: "fly", side: "call", net: 0.25, status: "settled" })], 1);
    expect(f.floorHolds).toBe(true);
    expect(f.worst).toBe(25);
    expect(f.worstTail).toBe("below");
    expect(f.worstAt).toBe(5995);
    expect(f.bandOpen).toEqual({ below: true, above: true });
  });

  it("calls a book locked when its worst equals its best everywhere", () => {
    // Two settled short verticals whose losses a pair of adjacent flies exactly cancel: the
    // 2026-09-11 control book shape, reduced. Net credits sum to 5.00 against a fixed -5 payoff.
    const book = [
      pos({ kind: "short_vertical", side: "put", center: 6010, net: 2.5, status: "settled" }),
      pos({ kind: "short_vertical", side: "call", center: 5995, net: 2.5, status: "settled" }),
      pos({ kind: "fly", side: "put", center: 6000, net: 0, status: "settled" }),
      pos({ kind: "fly", side: "call", center: 6005, net: 0, status: "settled" }),
    ];
    const f = bookFloor(book, 1);
    expect(f.locked).toBe(true);
    expect(f.worst).toBe(0);
    expect(f.floorHolds).toBe(true);
  });

  it("marks a band open on the side where it runs off the scan grid", () => {
    // Settled short call spread 6000/6005, credit 1.00: +100 below 6000, -400 above 6005.
    const f = bookFloor([pos({ kind: "short_vertical", side: "call", net: 1, status: "settled" })], 1);
    expect(f.band![0]).toBe(5985);
    expect(f.bandOpen).toEqual({ below: true, above: false });
    expect(f.worstTail).toBe("above");
    expect(f.worstAt).toBe(6005);
  });

  it("does not know a 'debit_vertical' kind -- fly.py raises on it, the ledger never writes it", () => {
    // The port must not carry a live-looking branch for a kind no row can have: an unknown kind
    // gets a payoff of 0, so a stray alias here would silently draw a flat line and read as data.
    expect(payoffOf(pos({ kind: "debit_vertical", side: "call" }), 6000)).toBe(0);
  });
});

// --------------------------------------------------------------------------- against the real ledger

interface LedgerRow {
  position_id: string;
  trade_date: string;
  arm: string;
  symbol: string;
  kind: string;
  side: string | null;
  center: number;
  wing_width: number;
  far_width: number | null;
  net: number;
  quantity: number;
  fees: number;
  status: string;
}

interface TheirFloor {
  worst: number;
  worst_at: number;
  floor_holds: boolean;
  band: [number, number] | null;
  bands: Array<[number, number]>;
  unbounded_below: boolean;
}

interface Theirs {
  pnl: Record<string, number[]>;
  floor: Record<string, number>;
  /** fly.book_floor per "trade_date|arm" group, at the display grid step payoff_curve would use. */
  book: Record<string, TheirFloor>;
}

/** Offsets from the centre in fifths of the wing width: both strikes, the ramps, and the tails. */
const OFFSETS = [-30, -10, -5, -2.5, 0, 2.5, 5, 10, 30];
/** How many of the most recent sessions the book-floor comparison covers, every arm of each. */
const SESSIONS = 15;

function ledgerRows(): LedgerRow[] {
  if (!fs.existsSync(LEDGER)) return [];
  const db = new Database(LEDGER, { readonly: true });
  try {
    const recent = db
      .prepare<[number], { trade_date: string }>(
        `SELECT DISTINCT trade_date FROM fly_positions WHERE status != 'voided' AND void_reason IS NULL
          ORDER BY trade_date DESC LIMIT ?`,
      )
      .all(SESSIONS)
      .map((r) => r.trade_date);
    if (recent.length === 0) return [];
    // Every long_vertical the ledger holds (the branch that drifted), plus the recent sessions whole.
    return db
      .prepare<string[], LedgerRow>(
        `SELECT position_id, trade_date, arm, symbol, kind, side, center, wing_width, far_width, net, quantity, fees, status
           FROM fly_positions
          WHERE status != 'voided' AND void_reason IS NULL
            AND (trade_date IN (${recent.map(() => "?").join(",")}) OR kind = 'long_vertical')
          ORDER BY trade_date, arm, position_id`,
      )
      .all(...recent);
  } finally {
    db.close();
  }
}

const MODULE_SCRIPT = [
  "import json, sys",
  "from cherrypick.flies import fly",
  "req = json.load(sys.stdin)",
  "out = {'pnl': {}, 'floor': {}, 'book': {}}",
  "def clean(r):",
  "    p = {k: r[k] for k in ('kind','side','center','wing_width','far_width','net','quantity','fees','status')}",
  "    if p.get('side') is None: p['side'] = 'put'",
  "    return p",
  "for r in req['rows']:",
  "    p = clean(r)",
  "    out['pnl'][r['position_id']] = [fly.position_pnl(p, s) for s in req['prices'][r['position_id']]]",
  "    out['floor'][r['position_id']] = fly.position_floor(p)",
  "for key, g in req['groups'].items():",
  "    b = fly.book_floor([clean(r) for r in g['rows']], step=g['step'])",
  "    out['book'][key] = {'worst': b['worst'], 'worst_at': b['worst_at'], 'floor_holds': b['floor_holds'],",
  "                        'band': list(b['band']) if b['band'] else None, 'bands': [list(z) for z in b['bands']],",
  "                        'unbounded_below': b['unbounded_below']}",
  "print(json.dumps(out))",
].join("\n");

/** The display-grid step payoff_curve derives for a book: max(1, span / 120) over centres +/- 3 wings. */
function displayStep(rows: LedgerRow[]): number {
  const centers = rows.map((r) => r.center);
  const width = Math.max(...rows.map((r) => r.far_width ?? r.wing_width));
  const span = Math.max(...centers) + 3 * width - (Math.min(...centers) - 3 * width);
  return span > 0 ? Math.max(1, span / 120) : 1;
}

function groupRows(rows: LedgerRow[]): Map<string, LedgerRow[]> {
  const groups = new Map<string, LedgerRow[]>();
  for (const r of rows) {
    const key = `${r.trade_date}|${r.arm}`;
    const list = groups.get(key) ?? [];
    list.push(r);
    groups.set(key, list);
  }
  return groups;
}

function askModule(rows: LedgerRow[], prices: Record<string, number[]>): Theirs | null {
  if (!fs.existsSync(path.join(FLIES_PKG, "run.py"))) return null;
  const groups: Record<string, { rows: LedgerRow[]; step: number }> = {};
  for (const [key, g] of groupRows(rows)) groups[key] = { rows: g, step: displayStep(g) };
  const out = spawnSync("python", ["-c", MODULE_SCRIPT], {
    cwd: FLIES_PKG,
    encoding: "utf-8",
    timeout: 120_000,
    input: JSON.stringify({ rows, prices, groups }),
    maxBuffer: 64 * 1024 * 1024,
  });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Theirs;
  } catch {
    return null;
  }
}

const rows = ledgerRows();
const grid: Record<string, number[]> = {};
for (const r of rows) grid[r.position_id] = OFFSETS.map((d) => r.center + d * (r.wing_width / 5));
const theirs = rows.length > 0 ? askModule(rows, grid) : null;
const available = rows.length > 0 && theirs !== null;

function toPosition(r: LedgerRow): FlyPosition {
  return {
    kind: r.kind,
    side: r.side ?? "put",
    center: r.center,
    wingWidth: r.wing_width,
    farWidth: r.far_width,
    net: r.net,
    quantity: r.quantity,
    fees: r.fees,
    status: r.status,
  };
}

describe.skipIf(!available)("the console's flies payoff mirror agrees with fly.py over the real ledger", () => {
  it("agrees on every position's P&L across its own price grid, to the cent", () => {
    for (const r of rows) {
      const prices = grid[r.position_id]!;
      const other = theirs!.pnl[r.position_id]!;
      prices.forEach((s, i) => {
        expect(positionPnl(toPosition(r), s), `${r.position_id} (${r.kind}/${r.side}) at ${s}`).toBeCloseTo(other[i]!, 2);
      });
    }
  });

  it("agrees on every position's floor, to the cent", () => {
    for (const r of rows) {
      expect(positionFloor(toPosition(r)), `${r.position_id} (${r.kind})`).toBeCloseTo(theirs!.floor[r.position_id]!, 2);
    }
  });

  it("agrees with fly.book_floor on every recent book's worst, band, zones and tails", () => {
    // The floor is computed on the module's strike-anchored scan grid, not the display grid, and
    // this is the check that told them apart: on the display grid the band's edges landed up to a
    // grid step off and the worst missed the assignment-fee step just past a strike.
    const groups = groupRows(rows);
    expect(groups.size).toBeGreaterThan(0);
    for (const [key, g] of groups) {
      const other = theirs!.book[key]!;
      const mine = bookFloor(g.map(toPosition), displayStep(g));
      expect(mine.worst, `${key} worst`).toBeCloseTo(other.worst, 2);
      expect(mine.floorHolds, `${key} floor_holds`).toBe(other.floor_holds);
      expect(mine.unboundedBelow, `${key} unbounded_below`).toBe(other.unbounded_below);
      expect(mine.bands, `${key} bands`).toEqual(other.bands);
      // `band` is the zone holding the payoff maximum. When two tents peak at the SAME net the
      // maximum is an exact tie and float summation order picks the zone, so the two sides may
      // legitimately name different zones -- but only when their peaks are equal to the cent.
      // (Making the rule deterministic means changing the module, whose pick is recorded on
      // fly_books.band_low/high and read by a classifier: a declared-boundary change, not this.)
      if (JSON.stringify(mine.band) !== JSON.stringify(other.band)) {
        expect(other.band, `${key} band`).not.toBeNull();
        expect(mine.band, `${key} band`).not.toBeNull();
        const ps = g.map(toPosition);
        const peak = (z: [number, number]) =>
          Math.max(...scanPrices(ps, displayStep(g)).filter((x) => x >= z[0] && x <= z[1]).map((x) => bookPnl(ps, x)));
        expect(peak(mine.band!), `${key} band tie`).toBeCloseTo(peak(other.band!), 2);
      }
    }
  });

  it("uses the same 2-leg vertical open fee the pre-completion rewind relies on", () => {
    const out = spawnSync("python", ["-c", "from cherrypick.flies import fly; print(fly.vertical_open_fee('SPX', 2))"], {
      cwd: FLIES_PKG,
      encoding: "utf-8",
      timeout: 60_000,
    });
    expect(out.status).toBe(0);
    expect(verticalOpenFee("SPX", 2)).toBeCloseTo(Number(out.stdout.trim()), 4);
  });

  it("book P&L is the plain sum of its positions", () => {
    const s = rows[0]!.center;
    const ps = rows.map(toPosition);
    expect(bookPnl(ps, s)).toBeCloseTo(ps.reduce((a, p) => a + positionPnl(p, s), 0), 6);
  });
});

describe("the mirror check itself", () => {
  it("says plainly when it could not run", () => {
    expect(typeof available).toBe("boolean");
    if (!available) {
      expect(fs.existsSync(LEDGER) === false || theirs === null).toBe(true);
    }
  });
});
