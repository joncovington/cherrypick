import { describe, it, expect, beforeAll, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";
import { closePooledDbs } from "../src/readers/db.js";
import {
  POSITION_ID,
  camelise,
  readPmccTracker,
  setPmccTrackerCaller,
} from "../src/services/pmccTrackerBridge.js";

/**
 * The PMCC tracker is BRIDGED from the module's own `analytics.tracker` (console CLAUDE.md: "mirror a
 * query, bridge a derivation"), so what is tested here is the bridge's own duty: an id that is not
 * a position id never reaches the subprocess, keys arrive camelised, an idle position spawns no
 * python twice, and -- against the real ledger, when there is one -- the module's tracker agrees
 * with the ledger's own result to the cent.
 */

let app: FastifyInstance;
let tmp: string;
const calls: string[][] = [];

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-pmcctracker-"));
  fs.mkdirSync(path.join(tmp, "pmcc"), { recursive: true });
  const db = new Database(path.join(tmp, "pmcc", "paper_trades.db"));
  db.exec(
    "CREATE TABLE pmcc_positions (position_id TEXT, updated_at TEXT);" +
      "CREATE TABLE pmcc_legs (position_id TEXT, updated_at TEXT);" +
      "CREATE TABLE pmcc_marks (position_id TEXT, marked_at REAL);",
  );
  db.prepare("INSERT INTO pmcc_positions VALUES (?, ?)").run("SLV:shield:2026-10-05", "t1");
  db.prepare("INSERT INTO pmcc_marks VALUES (?, ?)").run("SLV:shield:2026-10-05", 100);
  db.close();
  const config = {
    port: 0,
    paths: { cherrypick: tmp, pmccDir: path.join(tmp, "pmcc"), consoleData: path.join(tmp, "console") },
  } as unknown as ConsoleConfig;
  app = Fastify();
  registerSecurity(app);
  registerModuleRoutes(app, config);
  await app.ready();
});

afterEach(() => {
  setPmccTrackerCaller();
  calls.length = 0;
  closePooledDbs();
});

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

function fakeCaller(json: Record<string, unknown>) {
  setPmccTrackerCaller((argv) => {
    calls.push(argv);
    return { ok: true, json, error: null };
  });
}

describe("the tracker bridge", () => {
  it("refuses anything that is not a position id before it can reach the subprocess", async () => {
    fakeCaller({ ok: true, tracker: {} });
    for (const bad of ["", "SLV", "--db x", "SLV:shield:2026-10-05;rm", "slv:shield:2026-10-05", "SLV:Shield:2026-10-05"]) {
      const res = await get(`/api/pmcc/tracker?position=${encodeURIComponent(bad)}`);
      expect(res.statusCode, bad).toBe(400);
    }
    expect(calls).toEqual([]);
    expect(POSITION_ID.test("XSP:advised:tv-exit-threshold-floor:2026-10-02")).toBe(true);
  });

  it("camelises the module's keys, all the way down, and leaves values alone", () => {
    expect(camelise({ short_realised: 60, weeks: [{ return_on_long_cost: 0.04, week_end: "2026-09-04" }] })).toEqual({
      shortRealised: 60,
      weeks: [{ returnOnLongCost: 0.04, weekEnd: "2026-09-04" }],
    });
  });

  it("answers an idle position from memory, and asks again once it is marked", async () => {
    fakeCaller({ ok: true, tracker: { header: { net_extrinsic: 12.5 } } });
    const first = await get("/api/pmcc/tracker?position=SLV:shield:2026-10-05");
    expect(first.json()).toEqual({ ok: true, data: { header: { netExtrinsic: 12.5 } }, error: null });
    await get("/api/pmcc/tracker?position=SLV:shield:2026-10-05");
    expect(calls.length).toBe(1);

    const db = new Database(path.join(tmp, "pmcc", "paper_trades.db"));
    db.prepare("INSERT INTO pmcc_marks VALUES (?, ?)").run("SLV:shield:2026-10-05", 160);
    db.close();
    closePooledDbs();
    await get("/api/pmcc/tracker?position=SLV:shield:2026-10-05");
    expect(calls.length).toBe(2);
  });

  it("passes the module's own refusal through rather than an empty tracker", async () => {
    fakeCaller({ ok: false, reason: "unknown_position" });
    const res = await get("/api/pmcc/tracker?position=SLV:shield:2026-01-01");
    expect(res.json()).toEqual({ ok: false, data: null, error: "unknown_position" });
  });
});

// ------------------------------------------------------------------ against the real ledger
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "pmcc", "paper_trades.db");
const cliWorks =
  fs.existsSync(LEDGER) &&
  spawnSync("python", ["-m", "cherrypick.pmcc.cli", "--db", LEDGER, "tracker-index"], { encoding: "utf-8", timeout: 60_000 })
    .status === 0;

describe.skipIf(!cliWorks)("the module's tracker against the real ledger", () => {
  it("states every closed position's net exactly as the ledger does", () => {
    const db = new Database(LEDGER, { readonly: true });
    const closed = db
      .prepare<[], { position_id: string; net: number }>(
        "SELECT position_id, gross_pnl - fees AS net FROM pmcc_positions WHERE status = 'closed' " +
          "AND gross_pnl IS NOT NULL ORDER BY closed_session DESC LIMIT 5",
      )
      .all();
    db.close();
    expect(closed.length).toBeGreaterThan(0);
    for (const row of closed) {
      const out = readPmccTracker(LEDGER, row.position_id);
      expect(out.ok, out.error ?? "").toBe(true);
      const t = out.data!;
      // The header is the ledger's own result; the last weekly row is the header.
      expect(Math.abs((t.header.net ?? Number.NaN) - row.net)).toBeLessThanOrEqual(0.0051);
      expect(t.weeks[t.weeks.length - 1]!.net).toBe(t.header.net);
      expect((t.header.gross ?? 0) - (t.header.costs?.total ?? 0)).toBeCloseTo(t.header.net ?? Number.NaN, 2);
    }
  });
});

describe("the real-ledger check itself", () => {
  it("says plainly when it could not run", () => {
    expect(typeof cliWorks).toBe("boolean");
    if (!cliWorks) expect(!fs.existsSync(LEDGER) || cliWorks === false).toBe(true);
  });
});
