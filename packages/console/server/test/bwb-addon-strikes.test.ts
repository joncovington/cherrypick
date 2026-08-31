/**
 * The add-on's strikes on an open BWB.
 *
 * A fired add-on turns the fly into a 1-3-2, so its two strikes are the shape of the position now
 * rather than a detail of how it got there — and the four books exist precisely to disagree about
 * WHEN to fire, which makes "fired at what" the comparison the open-trades table is for. The row
 * carried them all along (`addon_short_strike`, `addon_long_strike`); the reader dropped them.
 *
 * Built against a fixture rather than the real ledger so it runs anywhere, and because the case
 * worth pinning — a fired position beside an unfired one — is not guaranteed to exist on any given
 * day in a live book.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it, beforeAll, afterAll } from "vitest";

import { readBwb } from "../src/readers/bwb.js";
import { closePooledDbs } from "../src/readers/db.js";
import type { ConsoleConfig } from "../src/config.js";

let dir: string;

/**
 * The module's own schema, verbatim from `bwb_positions` and every other table the reader touches.
 *
 * Copied rather than trimmed by hand, because a hand-trimmed fixture fails in the worst available
 * way here: `withReadOnlyDb` swallows a throw into the empty fallback, so ONE missing column
 * returns a clean, plausible, entirely empty payload instead of an error. Building this test cost
 * three rounds of exactly that -- a missing `bwb_loop_iterations`, then `measurement_breaks` (not
 * bwb-prefixed, so a grep for the module's tables misses it), then `bwb_marks.session_date` -- and
 * each one presented identically, as "no open positions". Which is what this package's own
 * instructions warn about: a broken reader is indistinguishable from an empty one.
 */
const DDL = `
CREATE TABLE bwb_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    symbol              TEXT NOT NULL,
    book                TEXT NOT NULL,
    entry_session       TEXT NOT NULL,
    structure_signature TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    expiration          TEXT NOT NULL,
    body_strike         REAL NOT NULL,
    near_strike         REAL NOT NULL,
    far_strike          REAL NOT NULL,
    entry_time          TEXT,
    entry_spot          REAL,
    entry_atm_strike    REAL,
    entry_expected_move REAL,
    entry_body_mid      REAL,
    entry_near_mid      REAL,
    entry_far_mid       REAL,
    entry_credit        REAL,
    entry_narrow_width  REAL,
    entry_wide_width    REAL,
    entry_max_loss      REAL,
    entry_dte           INTEGER,
    entry_cost          REAL,
    entry_slippage      REAL,
    advice_params       TEXT,
    -- persisted trigger latches
    peak_abs_delta      REAL,
    below_flip_seen     INTEGER NOT NULL DEFAULT 0,
    armed_at            TEXT,
    arm_reason          TEXT,
    addon_fired_at      TEXT,
    addon_short_strike  REAL,
    addon_long_strike   REAL,
    addon_credit        REAL,
    addon_cost          REAL,
    addon_slippage      REAL,
    status              TEXT NOT NULL DEFAULT 'open',
    exit_reason         TEXT,
    closed_at           TEXT,
    closed_session      TEXT,
    settlement_spot     REAL,
    itm_settlements     INTEGER,
    gross_pnl           REAL,
    fees                REAL,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE TABLE bwb_legs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL,
    leg_role        TEXT NOT NULL,
    occ_symbol      TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL,
    action          TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    entry_bid       REAL,
    entry_ask       REAL,
    entry_mid       REAL,
    entry_iv        REAL,
    entry_delta     REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    close_kind      TEXT,
    closed_at       TEXT,
    close_bid       REAL,
    close_ask       REAL,
    close_value     REAL,
    created_at      TEXT,
    updated_at      TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE bwb_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    leg_role     TEXT,
    marked_at    REAL NOT NULL,
    session_date TEXT NOT NULL,
    bid          REAL,
    ask          REAL,
    mid          REAL,
    delta        REAL,
    iv           REAL,
    spot         REAL,
    close_cost   REAL,
    quote_age_s  REAL,
    usable       INTEGER NOT NULL DEFAULT 0,
    refusal      TEXT
);
CREATE TABLE bwb_trigger_ticks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_session       TEXT NOT NULL,
    structure_signature TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    ticked_at           REAL NOT NULL,
    session_date        TEXT NOT NULL,
    near_abs_delta      REAL,
    peak_abs_delta      REAL,
    spot                REAL,
    gamma_flip          REAL,
    gamma_flip_basis    TEXT,
    below_flip_seen     INTEGER NOT NULL DEFAULT 0,
    addon_short_bid     REAL,
    addon_short_ask     REAL,
    addon_long_bid      REAL,
    addon_long_ask      REAL,
    measured            INTEGER NOT NULL DEFAULT 0,
    refusal             TEXT
, spot_measured INTEGER NOT NULL DEFAULT 0, flip_measured INTEGER NOT NULL DEFAULT 0);
CREATE TABLE bwb_management_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    occurred_at  REAL NOT NULL,
    session_date TEXT NOT NULL,
    action       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    executed     INTEGER NOT NULL DEFAULT 0,
    gate         TEXT,
    detail_json  TEXT
);
CREATE TABLE bwb_entry_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    book         TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    block_detail TEXT,
    spot         REAL,
    body_strike  REAL,
    near_strike  REAL,
    far_strike   REAL,
    credit       REAL
);
CREATE TABLE bwb_loop_iterations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at         REAL NOT NULL,
    session_date   TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL,
    open_positions INTEGER,
    marks_written  INTEGER,
    actions_taken  INTEGER,
    note           TEXT
);
CREATE TABLE measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    recorded_at REAL,
    UNIQUE(break_date, key)
);
`;

beforeAll(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bwb-addon-"));
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(DDL);
  const ins = db.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, book, entry_session, structure_signature,
       quantity, expiration,
       body_strike, near_strike, far_strike, entry_time, entry_spot, entry_credit, entry_max_loss,
       entry_cost, peak_abs_delta, below_flip_seen, armed_at, addon_fired_at, addon_short_strike,
       addon_long_strike, addon_credit, addon_cost, status)
     VALUES (?, 'SPX', ?, '2026-08-31', 'SPX-7675-7670-7660-2026-09-04', 1, '2026-09-04',
             7670, 7675, 7660,
             '2026-08-31T09:35:00-04:00', 7672, 2.50, 1000, 6.89, ?, 0, ?, ?, ?, ?, ?, ?, 'open')`,
  );
  // control never fires by design; delta fired and carries both strikes.
  ins.run("p-control", "control", 0.21, null, null, null, null, null, null);
  ins.run("p-delta", "delta", 0.44, "2026-08-31T10:20:00-04:00", "2026-08-31T10:27:24-04:00", 7665, 7655, 4.1, 3.44);
  db.close();
});

afterAll(() => {
  closePooledDbs();
  // Best-effort: on Windows the pooled handle can still be closing when this runs, and a failed
  // cleanup of a temp directory is not a test result.
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* the OS will reap it */
  }
});

const read = () => readBwb({ paths: { bwbDir: dir } } as unknown as ConsoleConfig);

const byBook = (book: string) => read().openPositions.find((p) => p.book === book);

describe("the add-on's strikes on an open position", () => {
  it("surfaces both strikes once the add-on has fired", () => {
    const p = byBook("delta");
    expect(p?.addonFiredAt).toBe("2026-08-31T10:27:24-04:00");
    expect(p?.addonShortStrike).toBe(7665);
    expect(p?.addonLongStrike).toBe(7655);
    expect(p?.addonCredit).toBeCloseTo(4.1, 5);
  });

  it("shorts the higher strike and buys the lower — it is a put CREDIT spread", () => {
    // Inverting these would describe a debit spread, which is a different trade with the opposite
    // risk. Worth pinning because the two columns are adjacent and easy to transpose.
    const p = byBook("delta");
    expect(p?.addonShortStrike).toBeGreaterThan(p?.addonLongStrike ?? Infinity);
  });

  it("leaves them null on a book that has not fired, rather than defaulting to a strike", () => {
    // control never fires by design, so this is the common row. A zero here would render as a real
    // strike of 0 and read as a position that does not exist.
    const p = byBook("control");
    expect(p?.addonFiredAt).toBeNull();
    expect(p?.addonShortStrike).toBeNull();
    expect(p?.addonLongStrike).toBeNull();
  });

  it("keeps the add-on's strikes apart from the fly's own", () => {
    // The fly and the add-on were priced at different moments — the fly at entry, the add-on when
    // the trigger fired — so they are separate fields, never merged into one strike set.
    const p = byBook("delta");
    expect([p?.nearStrike, p?.bodyStrike, p?.farStrike]).toEqual([7675, 7670, 7660]);
    expect([p?.addonShortStrike, p?.addonLongStrike]).toEqual([7665, 7655]);
  });
});
