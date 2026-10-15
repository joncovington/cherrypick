import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicLive } from "../src/readers/meicLive.js";

/**
 * The Live page's meic tab reads MEIC's live loop off files alone. What it must not blur: a
 * working entry order is not a position, a cancelled entry is in neither table, a side with a
 * recorded close cost is closed, a settlement never recorded is n/r (never zero), and an absent
 * ledger is not a failed read. Money is meic.ts's `meicTradeCash`, so every history row must add
 * up: entry + exit = gross, gross − fees − settlement = net.
 */

// 12:00 ET on 2026-10-15.
const NOON = new Date("2026-10-15T16:00:00Z");
// 17:30 ET, past the default 17:00 disarm.
const EVENING = new Date("2026-10-15T21:30:00Z");

function home(): { cfg: ConsoleConfig; root: string; meic: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "meic-live-"));
  const meic = path.join(root, "data", "meic");
  fs.mkdirSync(meic, { recursive: true });
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  return { cfg: { paths: { cherrypick: root, meicDir: meic } } as unknown as ConsoleConfig, root, meic };
}

const COLUMNS = [
  "id INTEGER PRIMARY KEY",
  "trade_date TEXT",
  "entry_time TEXT",
  "symbol TEXT",
  "arm TEXT",
  "status TEXT",
  "put_strike REAL",
  "call_strike REAL",
  "wing_width REAL",
  "quantity INTEGER",
  "net_credit REAL",
  "put_credit REAL",
  "call_credit REAL",
  "put_stop_cost REAL",
  "call_stop_cost REAL",
  "put_stop_fill_status TEXT",
  "call_stop_fill_status TEXT",
  "fill_confirmed_at TEXT",
  "pnl REAL",
  "fees REAL",
  "settlement_fees REAL",
  "exit_reason TEXT",
  "iv_rank_at_entry REAL",
  "dollar_multiplier REAL",
  "slippage_dollars REAL",
  "put_settle_value REAL",
  "call_settle_value REAL",
];

type Row = Partial<Record<string, string | number | null>>;

function ledger(dir: string, rows: Row[]): void {
  const db = new Database(path.join(dir, "meic_trades.db"));
  db.exec(`CREATE TABLE ic_trades (${COLUMNS.join(", ")})`);
  const names = COLUMNS.map((c) => c.split(" ")[0]!);
  const ins = db.prepare(`INSERT INTO ic_trades (${names.join(", ")}) VALUES (${names.map(() => "?").join(", ")})`);
  for (const r of rows) {
    ins.run(...names.map((n) => (n === "dollar_multiplier" ? (r[n] ?? 100) : (r[n] ?? null))));
  }
  db.close();
}

const IC = { symbol: "XSP", arm: "live", put_strike: 660, call_strike: 672, wing_width: 2, quantity: 1 };

describe("readMeicLive", () => {
  it("splits open from history, keeps a working order out of the positions, and leaves cancelled rows out", () => {
    const { cfg, meic } = home();
    ledger(meic, [
      // Open, put side stopped out (its close cost is recorded), call side still held.
      { ...IC, id: 1, trade_date: "2026-10-15", entry_time: "2026-10-15 12:31:00 ET", status: "partial",
        net_credit: 0.29, put_credit: 0.12, call_credit: 0.17, put_stop_cost: 0.3, fill_confirmed_at: "2026-10-15 12:31:05 ET" },
      // A working entry: the credit is the limit asked.
      { ...IC, id: 2, trade_date: "2026-10-15", status: "pending", net_credit: 0.31 },
      // Left open from an earlier session, call side closing at the broker.
      { ...IC, id: 3, trade_date: "2026-10-14", status: "open", net_credit: 0.25, put_credit: 0.1, call_credit: 0.15,
        call_stop_fill_status: "pending" },
      { ...IC, id: 4, trade_date: "2026-10-15", status: "cancelled", net_credit: 0.3, exit_reason: "unfilled_rejected" },
      // Stopped: a stop is a close, settlement recorded as zero.
      { ...IC, id: 5, trade_date: "2026-10-13", status: "stopped", net_credit: 0.35, put_credit: 0.14, call_credit: 0.21,
        pnl: -40, fees: 6.5, settlement_fees: 0, exit_reason: "stop_triggered" },
      // Expired with a side in the money: settled, $5 of the fee total is settlement.
      { ...IC, id: 6, trade_date: "2026-10-12", status: "expired", net_credit: 0.25, put_credit: 0.1, call_credit: 0.15,
        pnl: 25, fees: 7.6, settlement_fees: 5, exit_reason: "expired", put_settle_value: 0.4 },
      // Force-closed on a ledger row that never recorded its settlement split.
      { ...IC, id: 7, trade_date: "2026-10-09", status: "force_closed", net_credit: 0.2, put_credit: 0.08, call_credit: 0.12,
        pnl: 10, fees: 3, exit_reason: "force_close" },
    ]);

    const p = readMeicLive(cfg, NOON);
    expect(p.session).toBe("2026-10-15");
    expect(p.ledger).toBe("ok");
    expect(p.open.map((r) => r.id)).toEqual([2, 1, 3]);
    expect(p.history.map((r) => r.id)).toEqual([5, 6, 7]);

    const partial = p.open.find((r) => r.id === 1)!;
    expect(partial).toMatchObject({ working: false, putHeld: false, callHeld: true, entryCash: 29, overdue: false, symbol: "XSP" });
    const working = p.open.find((r) => r.id === 2)!;
    expect(working).toMatchObject({ working: true, entryCash: null, netCredit: 0.31, putHeld: false, callHeld: false });
    const overdue = p.open.find((r) => r.id === 3)!;
    expect(overdue).toMatchObject({ overdue: true, putHeld: true, callHeld: true, callClosing: true, putClosing: false });

    const stopped = p.history.find((r) => r.id === 5)!;
    expect(stopped).toMatchObject({ entryCash: 35, exitCash: -75, gross: -40, fees: 6.5, settlementFees: 0, net: -46.5, exitKind: "closed" });
    const settled = p.history.find((r) => r.id === 6)!;
    expect(settled).toMatchObject({ entryCash: 25, exitCash: 0, gross: 25, fees: 2.6, settlementFees: 5, net: 17.4, exitKind: "settled" });
    const forced = p.history.find((r) => r.id === 7)!;
    expect(forced).toMatchObject({ gross: 10, fees: 3, settlementFees: null, net: 7, exitKind: "closed" });

    for (const r of p.history) {
      expect(Math.round(((r.entryCash ?? 0) + (r.exitCash ?? 0)) * 100) / 100).toBe(r.gross);
      expect(Math.round(((r.gross ?? 0) - (r.fees ?? 0) - (r.settlementFees ?? 0)) * 100) / 100).toBe(r.net);
    }

    // One row never recorded settlement, so the total is n/r, and what was recorded still closes
    // the identity.
    expect(p.totals).toMatchObject({ trades: 3, sessions: 3, gross: -5, fees: 12.1, settlementFees: null, settlementRecorded: 5, net: -22.1 });
    expect(Math.round((p.totals.gross - p.totals.fees - p.totals.settlementRecorded) * 100) / 100).toBe(p.totals.net);
  });

  it("reads the arm record, the disarm time and the halt flag as separate facts", () => {
    const { cfg, root } = home();
    fs.writeFileSync(path.join(root, "config", "meic.json"), JSON.stringify({ enable_live_trading: true, live: { symbol: "XSP", disarm_time: "16:30" } }));
    fs.writeFileSync(path.join(root, "state", "meic-live-arm.json"), JSON.stringify({ date: "2026-10-15", at: "2026-10-15T07:46:27-04:00", armed_by: "live-meic-start" }));
    let p = readMeicLive(cfg, NOON);
    expect(p.arm).toMatchObject({ armed: true, stale: false, date: "2026-10-15", disarmTime: "16:30", pastDisarm: false, halted: false, liveEnabled: true, symbol: "XSP" });
    expect(p.ledger).toBe("absent");

    fs.writeFileSync(path.join(root, "state", "halt-live.flag"), "");
    p = readMeicLive(cfg, EVENING);
    expect(p.arm).toMatchObject({ armed: true, pastDisarm: true, halted: true });

    fs.writeFileSync(path.join(root, "state", "meic-live-arm.json"), JSON.stringify({ date: "2026-10-14" }));
    p = readMeicLive(cfg, NOON);
    expect(p.arm).toMatchObject({ armed: false, stale: true, date: "2026-10-14" });
  });

  it("defaults the disarm time to 17:00 and an unreadable config to unknown, never off", () => {
    const { cfg } = home();
    const p = readMeicLive(cfg, EVENING);
    expect(p.arm).toMatchObject({ armed: false, stale: false, date: null, disarmTime: "17:00", pastDisarm: true, liveEnabled: null, symbol: null });
  });

  it("reads the orphan, hold and broker-health files beside the ledger", () => {
    const { cfg, meic } = home();
    fs.writeFileSync(
      path.join(meic, "live_orphans.json"),
      JSON.stringify({ at: "2026-10-15T12:00:00-04:00", orphans: [{ order_id: 4417, status: "Live", underlying_symbol: "XSP", external_identifier: null }] }),
    );
    fs.writeFileSync(
      path.join(meic, "live_held.json"),
      JSON.stringify({ unresolved: { "ext-b": "timeout", "ext-a": "timeout" }, unrecorded: { "ext-c": { order_id: 99 } } }),
    );
    fs.writeFileSync(path.join(meic, "live_broker_health.json"), JSON.stringify({ last_ok_at: "2026-10-15T15:59:00+00:00", failing_since: null, failures: 0 }));
    const p = readMeicLive(cfg, NOON);
    expect(p.orphans).toEqual({
      at: "2026-10-15T12:00:00-04:00",
      orders: [{ orderId: "4417", status: "Live", underlyingSymbol: "XSP", externalIdentifier: null }],
    });
    expect(p.held).toEqual({ unresolved: ["ext-a", "ext-b"], unrecorded: [{ ext: "ext-c", orderId: "99" }] });
    expect(p.brokerHealth).toMatchObject({ lastOkAt: "2026-10-15T15:59:00+00:00", failingSince: null, failures: 0 });
  });

  it("reports an unreadable hold file as held, and absent files as nothing", () => {
    const { cfg, meic } = home();
    let p = readMeicLive(cfg, NOON);
    expect(p.orphans).toEqual({ at: null, orders: [] });
    expect(p.held).toEqual({ unresolved: [], unrecorded: [] });
    expect(p.brokerHealth).toBeNull();
    fs.writeFileSync(path.join(meic, "live_held.json"), "{not json");
    p = readMeicLive(cfg, NOON);
    expect(p.held.unresolved).toHaveLength(1);
  });

  it("tells an absent ledger from a failed read", () => {
    const { cfg, meic } = home();
    expect(readMeicLive(cfg, NOON).ledger).toBe("absent");
    fs.writeFileSync(path.join(meic, "meic_trades.db"), "not a database");
    const p = readMeicLive(cfg, NOON);
    expect(p.ledger).toBe("failed");
    expect(p.ledgerError).not.toBeNull();
    expect(p.open).toEqual([]);
  });
});
