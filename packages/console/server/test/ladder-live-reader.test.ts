import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Fastify from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { ladderLiveDbPath, maskAccount, readLadderLive, redactAccounts } from "../src/readers/ladderLive.js";
import { registerModuleRoutes } from "../src/routes/modules.js";
import { registerSecurity } from "../src/security.js";
import { emptyLiveLedger, FAKE_ACCOUNT, FAKE_MASKED, insert, journal, openLot, populatedLiveLedger, soldLot } from "./ladderLiveFixture.js";

/**
 * The ladder's LIVE ledger reader. A machine that never ran the live loop has no file, and that is an
 * empty state and not an error; a dry-run machine has a journal and nothing else; lots are the module's
 * own money and add up as stored; an account never leaves the reader unmasked.
 */

const NOW = new Date("2026-10-15T18:00:00Z"); // 14:00 ET
let dir: string;
let config: ConsoleConfig;

beforeEach(() => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "console-ladder-live-"));
  dir = path.join(root, "ladder");
  fs.mkdirSync(dir);
  config = { paths: { ladderDir: dir, cherrypick: root } } as unknown as ConsoleConfig;
});

afterEach(() => closePooledDbs());

describe("the live ledger's state", () => {
  it("is absent, not an error, where the live loop has never run", () => {
    const p = readLadderLive(config, NOW);
    expect(p).toMatchObject({ ledger: "absent", ledgerError: null, openLots: [], closedLots: [], orders: [], journal: [], lastIteration: null });
    expect(p.closedTotals).toMatchObject({ positions: 0, net: 0 });
    expect(p.orphans).toEqual({ at: null, count: 0 });
  });

  it("is failed, and says why, when the file is not a database", () => {
    fs.writeFileSync(ladderLiveDbPath(config), "not a database");
    const p = readLadderLive(config, NOW);
    expect(p.ledger).toBe("failed");
    expect(p.ledgerError).toBeTruthy();
    expect(p.openLots).toEqual([]);
  });

  it("reads a dry-run-only ledger as a journal and no book", () => {
    const db = emptyLiveLedger(dir);
    journal(db);
    journal(db, { mode: "blocked", reason: "positions_shortfall: broker holds 0, ledger 20", accepted: 0 });
    db.close();
    const p = readLadderLive(config, NOW);
    expect(p.ledger).toBe("ok");
    expect(p.openLots).toEqual([]);
    expect(p.closedLots).toEqual([]);
    expect(p.orders).toEqual([]);
    expect(p.closedTotals.positions).toBe(0);
    expect(p.journal.map((j) => j.mode)).toEqual(["blocked", "dry_run"]); // newest first
  });

  it("never reads the paper ledger beside it", () => {
    // A paper lot in paper_trades.db is not a live lot, whatever sits in the same folder.
    const paper = emptyLiveLedger(dir);
    paper.close();
    fs.renameSync(ladderLiveDbPath(config), path.join(dir, "paper_trades.db"));
    expect(readLadderLive(config, NOW).ledger).toBe("absent");
  });

  it("reads the orphan sweep and nothing else from the sweep's file", () => {
    fs.writeFileSync(path.join(dir, "live_orphans.json"), JSON.stringify({ at: "2026-10-15T13:00:00-04:00", orphans: [{ order_id: 7 }, { order_id: 8 }] }));
    expect(readLadderLive(config, NOW).orphans).toEqual({ at: "2026-10-15T13:00:00-04:00", count: 2 });
  });
});

describe("open and sold live lots", () => {
  it("lists the lots held with their age, hospital flag and the sell resting at the broker", () => {
    populatedLiveLedger(dir).close();
    const p = readLadderLive(config, NOW);
    const [recent, old] = p.openLots; // newest buy first
    expect(recent).toMatchObject({ positionId: "session:TQQQ:2026-10-15:1", symbol: "TQQQ", quantity: 20, entryValue: -1000, daysHeld: 0, hospital: false });
    expect(recent!.sell).toEqual({ side: "sell", state: "working", limit: 52, tif: "GTC_EXT", final: false });
    expect(old).toMatchObject({ symbol: "SOXL", daysHeld: 25, hospital: true, sell: null });
  });

  it("states a sold lot's money so it adds up, fees split by buy and sale with their source", () => {
    populatedLiveLedger(dir).close();
    const p = readLadderLive(config, NOW);
    expect(p.closedLots.map((r) => r.positionId)).toEqual(["session:TQQQ:2026-10-14:1", "session:TQQQ:2026-10-01:1"]); // newest sale first
    for (const r of p.closedLots) {
      expect(r.entryCash! + r.exitCash!).toBeCloseTo(r.grossPnl!, 2);
      expect(r.grossPnl! - r.fees! - r.settlementFees! - r.slippage!).toBeCloseTo(r.netPnl!, 2);
      expect(r.buyFees! + r.sellFees!).toBeCloseTo(r.fees!, 2);
    }
    expect(p.closedLots[0]).toMatchObject({ entryCash: -960, exitCash: 998.4, grossPnl: 38.4, fees: 0.07, netPnl: 38.33, feesSource: "broker", exitKind: "closed" });
    expect(p.closedLots[1]).toMatchObject({ feesSource: "modeled" });
  });

  it("totals every sold lot with the same arithmetic as the rows, and counts the fee sources", () => {
    populatedLiveLedger(dir).close();
    const p = readLadderLive(config, NOW);
    expect(p.closedTotals).toEqual({ positions: 2, gross: 56.4, fees: 0.11, settlementFees: 0, slippage: 0, net: 56.29 });
    expect(p.closedTotals.gross - p.closedTotals.fees - p.closedTotals.settlementFees - p.closedTotals.slippage).toBeCloseTo(p.closedTotals.net, 2);
    expect(p.feesBySource).toEqual({ modeled: 1, broker: 1, unrecorded: 0 });
  });

  it("shows a fee that was never recorded as n/r and not as zero", () => {
    const db = emptyLiveLedger(dir);
    soldLot(db, "session:TQQQ:2026-10-14:9", { fees: null, net_pnl: null, buy_fees: null, sell_fees: null, fees_source: null });
    db.close();
    const p = readLadderLive(config, NOW);
    expect(p.closedLots[0]).toMatchObject({ fees: null, netPnl: null, feesSource: null });
    expect(p.feesBySource).toEqual({ modeled: 0, broker: 0, unrecorded: 1 });
  });

  it("lists working orders before ended ones, and leaves an open lot out of the sold table", () => {
    populatedLiveLedger(dir).close();
    const p = readLadderLive(config, NOW);
    expect(p.orders.map((o) => [o.clientId, o.final])).toEqual([
      ["ldr.s.TQQQ.20261015.1", false],
      ["ldr.b.TQQQ.20261015.2", false],
      ["ldr.b.TQQQ.20261015.1", true],
    ]);
    expect(p.closedLots.every((r) => r.sellSession !== null)).toBe(true);
  });

  it("reports the live loop's own last tick", () => {
    populatedLiveLedger(dir).close();
    const it2 = readLadderLive(config, NOW).lastIteration!;
    expect(it2).toMatchObject({ phase: "session", status: "ok" });
    expect(it2.ageSeconds).toBe(7260); // tick 15:59Z, now 18:00Z
  });
});

describe("accounts stay masked", () => {
  it("masks the account on every lot and order, and in the journal's words and detail", () => {
    populatedLiveLedger(dir).close();
    const p = readLadderLive(config, NOW);
    expect(JSON.stringify(p)).not.toContain(FAKE_ACCOUNT);
    expect(p.openLots[0]!.account).toBe(FAKE_MASKED);
    expect(p.closedLots[0]!.account).toBe(FAKE_MASKED);
    expect(p.orders[0]!.account).toBe(FAKE_MASKED);
    const refused = p.journal.find((j) => j.mode === "refused")!;
    expect(refused.reason).toContain(`account=${FAKE_MASKED}`);
    expect(p.journal.find((j) => j.mode === "alert")!.detail).toContain(FAKE_MASKED);
  });

  it("masks the account in an order carrying one the module forgot to mask", () => {
    const db = emptyLiveLedger(dir);
    openLot(db, "session:TQQQ:2026-10-15:7", { account: "99887766" });
    insert(db, "ladder_orders", { client_id: "ldr.b.TQQQ.20261015.7", side: "buy", symbol: "TQQQ", shares: 1, limit_price: 1, account: "99887766" });
    db.close();
    const p = readLadderLive(config, NOW);
    expect(JSON.stringify(p)).not.toContain("99887766");
    expect(p.openLots[0]!.account).toBe("****7766");
  });

  it("keeps an already-masked value, gives nothing for none, and masks a short one whole", () => {
    expect(maskAccount("****1234")).toBe("****1234");
    expect(maskAccount(null)).toBeNull();
    expect(maskAccount("")).toBeNull();
    expect(maskAccount("abc")).toBe("****");
    expect(redactAccounts(`{"account_number": "${FAKE_ACCOUNT}", "n": 5}`)).toBe(`{"account_number": "${FAKE_MASKED}", "n": 5}`);
    expect(redactAccounts(`account=${FAKE_ACCOUNT} next`)).toBe(`account=${FAKE_MASKED} next`);
    // A bare token that merely looks like an account is left alone, as core.redact leaves it.
    expect(redactAccounts("order 5WZ04242 working")).toBe("order 5WZ04242 working");
  });
});

describe("the routes", () => {
  it("serve the live ledger on a GET and take no verb", async () => {
    populatedLiveLedger(dir).close();
    const app = Fastify();
    registerSecurity(app);
    registerModuleRoutes(app, config);
    await app.ready();
    const host = { host: "127.0.0.1:5070" };
    const ok = await app.inject({ method: "GET", url: "/api/ladder/live", headers: host });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().ledger).toBe("ok");
    for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
      for (const url of ["/api/ladder/live", "/api/ladder/live/status", "/api/ladder/live/arm", "/api/ladder/live/halt"]) {
        const res = await app.inject({ method, url, headers: { ...host, "content-type": "application/json" }, payload: "{}" });
        expect(res.statusCode, `${method} ${url}`).toBeGreaterThanOrEqual(400);
      }
    }
    await app.close();
  }, 60_000);
});
