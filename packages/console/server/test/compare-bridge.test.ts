import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import type { CompareScope } from "@console/shared";
import type { ConsoleConfig } from "../src/config.js";
import { clearMemoOnStore } from "../src/readers/db.js";
import { registerCompareRoutes } from "../src/routes/compare.js";
import { spawnModuleCli } from "../src/services/moduleCli.js";
import { compareStores, readCompare, setCompareCaller, shapeCompare, type CompareResult } from "../src/services/compareBridge.js";

/**
 * The console asks `core.compare` for every arm beside its control rather than re-deriving a
 * bootstrap interval, a paired t bound and a sign test in TypeScript. What these pin is the
 * console's side: the document is handed on with the bridge's own field names, a bad scope is
 * refused rather than read as the default, every failure is `{ok: false, error}` (never an empty
 * table), and the memo moves when any ledger the bridge reads moves.
 *
 * The fixture is the live `--scope era` document of 2026-10-12, trimmed:
 * five flies arms, curve's one advised arm, pmcc with no arm in its era, and a synthetic absent
 * and failed module in the bridge's own shape.
 */

const FIXTURE = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, "fixtures", "compare-era.json"), "utf-8")) as Record<
  string,
  unknown
>;

let tmp: string;
let config: ConsoleConfig;
let app: FastifyInstance;
let calls: Array<[CompareScope, string]> = [];

function okCaller(scope: CompareScope, session: string): CompareResult {
  calls.push([scope, session]);
  return shapeCompare({ ...FIXTURE, scope }, null);
}

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cmp-bridge-"));
  const dir = (m: string) => path.join(tmp, "data", m);
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      fliesDir: dir("flies"),
      meicDir: dir("meic"),
      bwbDir: dir("bwb"),
      curveDir: dir("curve"),
      calendarsDir: dir("calendars"),
      pmccDir: dir("pmcc"),
      earningsDir: dir("earnings"),
      contangoDir: dir("contango"),
      advisorDir: dir("advisor"),
    },
  } as unknown as ConsoleConfig;
  app = Fastify();
  registerCompareRoutes(app, config);
  await app.ready();
});

afterAll(async () => {
  await app.close();
  fs.rmSync(tmp, { recursive: true, force: true });
});

afterEach(() => {
  setCompareCaller();
  clearMemoOnStore();
  calls = [];
});

describe("shaping the bridge's document", () => {
  it("hands the document on with the bridge's snake_case names intact", () => {
    const out = shapeCompare(FIXTURE, null);
    expect(out.ok).toBe(true);
    expect(out.error).toBeNull();
    expect(out.data).not.toHaveProperty("ok");
    expect(out.data?.costs_note).toMatch(/^costs = /);
    const flies = out.data?.modules.find((m) => m.module === "flies");
    const down = flies?.arms.find((a) => a.arm === "debit-first-down");
    expect(down?.vs_control?.sessions_to_exclude_zero).toBe(10);
    expect(down?.vs_control?.sign_test_p).toBe(0.6875);
    expect(down?.session_net_ci).toEqual([-464.49, 11.48]);
    // A null stays a null: "could not be computed" is not zero.
    const range = flies?.arms.find((a) => a.arm === "range-high");
    expect(range?.vs_control?.ci).toBeNull();
    expect(range?.vs_control?.sign_test_p).toBeNull();
  });

  it("reports the CLI's own refusal as an error", () => {
    const out = shapeCompare({ ok: false, error: "ValueError: compare: scope must be 'era' or 'all'" }, null);
    expect(out).toEqual({ ok: false, data: null, error: "ValueError: compare: scope must be 'era' or 'all'" });
  });

  it("refuses a schema it cannot read rather than half-render it", () => {
    const out = shapeCompare({ ...FIXTURE, schema: 2 }, null);
    expect(out.ok).toBe(false);
    expect(out.error).toMatch(/schema 2/);
  });

  it("refuses a document with no module list", () => {
    const { modules: _m, ...rest } = FIXTURE;
    expect(shapeCompare(rest, null).ok).toBe(false);
  });
});

describe("GET /api/compare", () => {
  it("defaults to the era and passes the session through", async () => {
    setCompareCaller(okCaller);
    const res = await app.inject({ method: "GET", url: "/api/compare" });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.ok).toBe(true);
    expect(body.scope).toBe("era");
    expect(body.data.modules.map((m: { module: string }) => m.module)).toEqual(["flies", "curve", "pmcc", "contango", "earnings"]);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.[0]).toBe("era");
    expect(calls[0]?.[1]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("reads `all` when asked", async () => {
    setCompareCaller(okCaller);
    const res = await app.inject({ method: "GET", url: "/api/compare?scope=all" });
    expect(res.json().scope).toBe("all");
    expect(calls[0]?.[0]).toBe("all");
  });

  it("refuses an unknown scope with 400 and never runs the bridge", async () => {
    setCompareCaller(okCaller);
    for (const scope of ["al", "ALL", "", "era;rm"]) {
      const res = await app.inject({ method: "GET", url: `/api/compare?scope=${encodeURIComponent(scope)}` });
      expect(res.statusCode, scope).toBe(400);
      expect(res.json().error).toMatch(/scope must be one of: era, all/);
    }
    expect(calls).toHaveLength(0);
  });

  it("a failed bridge is {ok: false, error}, never an empty document", async () => {
    setCompareCaller(() => ({ ok: false, data: null, error: "arm comparison unavailable — No module named cherrypick.core.compare" }));
    const res = await app.inject({ method: "GET", url: "/api/compare" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({
      ok: false,
      scope: "era",
      data: null,
      error: "arm comparison unavailable — No module named cherrypick.core.compare",
    });
  });
});

describe("the CLI's own failure reaches the page", () => {
  // core.compare prints {"ok": false, "error"} on stdout and exits 1 with an empty stderr. Run a
  // real subprocess (node standing in for python) so the spawn helper's nonzero-exit branch is the
  // one under test: before it read stdout, the page ended its message at the dash where the module
  // had said what broke (an empty stderr is not nullish, so even the `exit 1` fallback never fired).
  const saved = process.env["CHERRYPICK_PYTHON"];
  afterEach(() => {
    if (saved === undefined) delete process.env["CHERRYPICK_PYTHON"];
    else process.env["CHERRYPICK_PYTHON"] = saved;
  });

  it("carries the stdout reason of a nonzero exit", () => {
    process.env["CHERRYPICK_PYTHON"] = process.execPath;
    const res = spawnModuleCli(
      ["-e", 'process.stdout.write(JSON.stringify({ok:false,error:"OperationalError: database is locked"})); process.exit(1)'],
      "unavailable",
    );
    expect(res.ok).toBe(false);
    expect(res.error).toBe("unavailable — OperationalError: database is locked");
  });

  it("still falls back to the last stderr line", () => {
    process.env["CHERRYPICK_PYTHON"] = process.execPath;
    const res = spawnModuleCli(["-e", 'process.stderr.write("first\\nModuleNotFoundError: x\\n"); process.exit(1)'], "unavailable");
    expect(res.error).toBe("unavailable — ModuleNotFoundError: x");
  });
});

describe("the memo", () => {
  it("stamps every module ledger and the advisor store", () => {
    const stores = compareStores(config).map((p) => path.relative(tmp, p).replace(/\\/g, "/"));
    expect(stores).toEqual([
      "data/flies/paper_trades.db",
      "data/meic/paper_trades.db",
      "data/bwb/paper_trades.db",
      "data/curve/paper_trades.db",
      "data/calendars/paper_trades.db",
      "data/pmcc/paper_trades.db",
      "data/earnings/paper_trades.db",
      "data/contango/paper_trades.db",
      "data/advisor/advisor.db",
    ]);
  });

  it("serves a poll from memory, and re-runs when any ledger moves or the session turns", () => {
    setCompareCaller(okCaller);
    readCompare(config, "era", "2026-10-12");
    readCompare(config, "era", "2026-10-12");
    expect(calls).toHaveLength(1);

    // A trade closing in the LAST store of the list -- one the memo would miss if it stamped only
    // the first.
    const contango = path.join(config.paths.contangoDir, "paper_trades.db");
    fs.mkdirSync(path.dirname(contango), { recursive: true });
    fs.writeFileSync(contango, "x");
    readCompare(config, "era", "2026-10-12");
    expect(calls).toHaveLength(2);

    readCompare(config, "era", "2026-10-13");
    expect(calls).toHaveLength(3);
    readCompare(config, "all", "2026-10-13");
    expect(calls).toHaveLength(4);
  });
});
