import { describe, it, expect, beforeAll, afterAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { armMeta, isBookModule, readBookGuide } from "../src/readers/experimentGuide.js";

/**
 * The arm guide for the book modules (2026-10-27, docs/progress-queue-arms-plan.md phase 3): the same
 * derivation flies and MEIC have, over calendars, pmcc, curve, contango and bwb -- their `books`/`arms`
 * registry, their `<prefix>_positions` ledger keyed by `entry_session`, and the newer one-setting-a-row
 * `measurement_breaks`. An arm's `_summary`, `_since` and `_retired` are fields, not notes.
 */

let tmp: string;
let config: ConsoleConfig;

const CURVE = {
  defaults: { short_delta_target: 0.3, profit_take_pct: 0.5 },
  books: {
    _books_note: "control is the strategy as pitched.",
    control: { enabled: true, _summary: "The VXX call credit spread on a contango day.", _note: "Enter on a contango day." },
    noflip: { _summary: "control without the flip exit." },
    hook: { enabled: false, _summary: "Retired: the backwardation hook.", _retired: "2026-10-15" },
    near: { enabled: true, short_delta_target: 0.4, _summary: "A 0.40-delta short.", _since: "2026-10-06" },
  },
};

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-bookguide-"));
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config", "curve.json"), JSON.stringify(CURVE));
  const dir = path.join(tmp, "curve");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE curve_positions (id INTEGER PRIMARY KEY, arm TEXT, entry_session TEXT);
    CREATE TABLE measurement_breaks (
      id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, old_value TEXT, new_value TEXT, note TEXT, recorded_at TEXT
    );
  `);
  const pos = db.prepare("INSERT INTO curve_positions (arm, entry_session) VALUES (?, ?)");
  pos.run("control", "2026-09-01");
  pos.run("control", "2026-10-20");
  pos.run("hook", "2026-09-05");
  pos.run("gone", "2026-08-28");
  db.prepare("INSERT INTO measurement_breaks (break_date, key, old_value, new_value, note) VALUES (?, ?, ?, ?, ?)").run(
    "2026-10-06",
    "spread_width",
    "1",
    "2",
    "the $2-wide boundary",
  );
  db.close();
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      curveDir: dir,
      advisorDir: path.join(tmp, "advisor"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
      meicRiskConfig: path.join(tmp, "config", "meic.risk.json"),
    },
  } as unknown as ConsoleConfig;
});

afterAll(() => {
  closePooledDbs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

const guide = () => readBookGuide(config, "curve");
const arm = (name: string) => guide().entries.find((e) => e.name === name)!;

describe("a book module's arm guide", () => {
  it("reads the registry, with each arm's one line, dates and ledger span", () => {
    const g = guide();
    expect(g.configMissing).toBe(false);
    expect(g.module).toBe("curve");
    expect(arm("control")).toMatchObject({
      summary: "The VXX call credit spread on a contango day.",
      enabled: true,
      positions: 2,
      firstSession: "2026-09-01",
      lastSession: "2026-10-20",
    });
    expect(arm("near")).toMatchObject({ since: "2026-10-06", enabled: true, positions: 0 });
    expect(g.groupNotes.map((n) => n.key)).toEqual(["books_note"]);
  });

  it("keeps the summary and dates out of the notes", () => {
    expect(arm("control").notes.map((n) => n.key)).toEqual(["note"]);
    expect(arm("hook").notes).toEqual([]);
  });

  it("defaults an arm to enabled as the engines do, and takes status from enabled, never from _retired", () => {
    expect(arm("noflip").enabled).toBe(true);
    expect(arm("hook")).toMatchObject({ enabled: false, retired: true, retiredOn: "2026-10-15" });
  });

  it("shows what makes an arm different from the defaults", () => {
    const delta = arm("near").overrides.find((o) => o.key === "short_delta_target")!;
    expect(delta).toMatchObject({ value: 0.4, fallback: 0.3, matchesDefault: false });
  });

  it("lists an arm the ledger has but the config lost", () => {
    expect(arm("gone")).toMatchObject({ removed: true, positions: 1, summary: null });
  });

  it("reads the newer one-setting-a-row measurement breaks", () => {
    expect(guide().breaks).toEqual([
      { date: "2026-10-06", scope: "*", kind: "spread_width", reason: "spread_width: 1 → 2. the $2-wide boundary" },
    ]);
  });

  it("says the config is missing rather than showing no arms", () => {
    expect(readBookGuide(config, "bwb").configMissing).toBe(true);
  });
});

describe("armMeta", () => {
  it("reads _summary, _since (or pmcc's from) and _retired, and only ISO dates", () => {
    expect(armMeta({ _summary: "  x  ", from: "2026-10-26" })).toEqual({ summary: "x", since: "2026-10-26", retiredOn: null });
    expect(armMeta({ _since: "Oct 6", _retired: "2026-10-15" })).toEqual({ summary: null, since: null, retiredOn: "2026-10-15" });
  });
});

describe("the route", () => {
  let app: FastifyInstance;
  beforeAll(async () => {
    const { registerModuleRoutes } = await import("../src/routes/modules.js");
    app = Fastify();
    registerModuleRoutes(app, config);
    await app.ready();
  });
  afterAll(async () => {
    await app.close();
  });

  it("serves a book module's guide and 404s anything else", async () => {
    const ok = await app.inject({ method: "GET", url: "/api/arms/guide/curve" });
    expect(ok.statusCode).toBe(200);
    expect((ok.json() as { module: string }).module).toBe("curve");
    for (const bad of ["flies", "meic", "..%2Fconfig", "earnings"]) {
      expect((await app.inject({ method: "GET", url: `/api/arms/guide/${bad}` })).statusCode).toBe(404);
    }
    expect(isBookModule("pmcc")).toBe(true);
    expect(isBookModule("toString")).toBe(false);
  });
});
