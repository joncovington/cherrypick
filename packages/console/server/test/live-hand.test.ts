import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readLiveHand } from "../src/readers/liveHand.js";

/**
 * Every figure here is made up. Live → today's
 * hand row reads packages/manage's `zero_dte` block from TODAY's evaluation and shapes
 * it, deriving nothing. Pinned: only today's evaluation counts (yesterday's hand trades are not
 * today's); a block manage could not build says why; an evaluation written before the block existed
 * says so rather than reading as a quiet day; the money is manage's figures, unchanged.
 */

const DAY = "2026-10-14";
let dir: string;
let config: ConsoleConfig;

function write(session: string, doc: unknown): void {
  fs.writeFileSync(path.join(dir, `eval-${session}.json`), JSON.stringify(doc));
}

const BLOCK = {
  session: DAY,
  underlyings: ["SPX"],
  read: true,
  error: null,
  orders: [
    {
      order_id: "200",
      at: "2026-10-14T18:10:00Z",
      kind: "open",
      legs: [{ symbol: "SPXW  261014P04980000", action: "Sell to Open", quantity: 1, price: 2.2, value: 220 }],
      cash: 220,
      fees: 2,
    },
  ],
  open_legs: [{ symbol: "SPXW  261014P04980000", right: "P", strike: 4980, quantity: -1, mid: 0, value: 0, mark_source: "feed", mark_age_seconds: 0 }],
  unreconciled: [],
  after_publish: [],
  known_modules: { meic: "2026-10-14T15:00:00-04:00" },
  loop_fills: 6,
  cash: 220,
  mark: 0,
  gross: 220,
  fees: 2,
  settlement: null,
  net: 218,
  net_basis: "at mark: held legs at mid, not yet settled",
  caveats: [],
};

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "live-hand-"));
  config = { paths: { manageDir: dir } } as unknown as ConsoleConfig;
});

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

describe("readLiveHand", () => {
  it("shapes today's block and keeps manage's money as it is", () => {
    write(DAY, { snapshot_generated_at: "2026-10-14T13:30:00-0400", remark: { at: "2026-10-14T15:59:00-04:00" }, accounts: [{ zero_dte: BLOCK }] });
    const h = readLiveHand(config, DAY);
    expect(h.status).toBe("ok");
    expect(h.markedAt).toBe("2026-10-14T15:59:00-04:00");
    expect(h.orders[0]).toMatchObject({ orderId: "200", kind: "open", cash: 220, fees: 2 });
    expect(h.openLegs[0]).toMatchObject({ quantity: -1, mid: 0, value: 0, markSource: "feed" });
    expect([h.cash, h.mark, h.gross, h.fees, h.settlement, h.net]).toEqual([220, 0, 220, 2, null, 218]);
    expect(h.loopFills).toBe(6);
    expect(h.knownModules).toEqual({ meic: "2026-10-14T15:00:00-04:00" });
  });

  it("reads only today's evaluation", () => {
    write("2026-10-13", { accounts: [{ zero_dte: { ...BLOCK, session: "2026-10-13" } }] });
    const h = readLiveHand(config, DAY);
    expect(h.status).toBe("none");
    expect(h.reason).toContain("newest 2026-10-13");
    expect(h.orders).toEqual([]);
    expect(h.net).toBeNull();
  });

  it("says why when manage could not build the block, or the evaluation predates it", () => {
    write(DAY, { accounts: [{ zero_dte: { ...BLOCK, read: false, error: "no loop order ids in this snapshot (taken before v16)", net: null } }] });
    expect(readLiveHand(config, DAY)).toMatchObject({ status: "unread", reason: "no loop order ids in this snapshot (taken before v16)", net: null });
    write(DAY, { accounts: [{ trades: [] }] });
    expect(readLiveHand(config, DAY).status).toBe("unread");
    expect(readLiveHand(config, DAY).reason).toContain("no 0DTE view");
  });

  it("takes the account that carries the block (manage sets it on the designated one only)", () => {
    write(DAY, { accounts: [{ zero_dte: null }, { zero_dte: BLOCK }] });
    expect(readLiveHand(config, DAY).net).toBe(218);
  });

  it("an unreadable evaluation is unread, never a quiet day", () => {
    fs.writeFileSync(path.join(dir, `eval-${DAY}.json`), "{not json");
    expect(readLiveHand(config, DAY)).toMatchObject({ status: "unread", reason: `eval-${DAY}.json is unreadable` });
  });
});
