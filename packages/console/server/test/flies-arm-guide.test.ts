import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesArmGuide } from "../src/readers/flies.js";

/**
 * The arm guide answers "what makes this arm different from the others", and the whole value of it
 * is that the answer is derived rather than written down twice. So these pin the derivation: notes
 * come from the module's own config, and an override only counts as distinguishing if it actually
 * distinguishes — a value every arm shares differs from `defaults` but from no sibling, and listing
 * it would bury the one setting that carries the hypothesis.
 */

let config: ConsoleConfig;

const FLIES_CONFIG = {
  defaults: { wing_width: 5, max_positions: 4, entry_windows: [] as unknown[] },
  arms: {
    _width_arms_note: "The width sweep, disabled with the move to SPX.",
    control: {
      enabled: true,
      entry_windows: [["10:00", "14:30"]],
      _note: "The shared baseline every comparison is read against.",
      _history_note: "Was a single 12:00-12:15 window with max_positions 2.",
    },
    bwb: {
      enabled: true,
      entry_windows: [["10:00", "14:30"]],
      center_rule: "gex",
      _note: "Isolates the entry construction.",
    },
    "width-5": {
      enabled: false,
      entry_windows: [["10:00", "14:30"]],
      wing_width: 5,
    },
  },
};

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-armguide-"));
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config", "flies.json"), JSON.stringify(FLIES_CONFIG, null, 2));

  const dir = path.join(tmp, "flies");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_positions (id INTEGER PRIMARY KEY, arm TEXT, trade_date TEXT);
    CREATE TABLE measurement_breaks (
      id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, reason TEXT, detail TEXT, created_at TEXT
    );
  `);
  const pos = db.prepare("INSERT INTO fly_positions (arm, trade_date) VALUES (?, ?)");
  pos.run("control", "2026-08-11");
  pos.run("control", "2026-08-13");
  pos.run("width-5", "2026-07-30");
  db.prepare("INSERT INTO measurement_breaks (break_date, scope, kind, reason) VALUES (?, ?, ?, ?)").run(
    "2026-08-09",
    "*",
    "cadence",
    "tick cadence 60s -> 15s at the supervisor cutover",
  );
  db.close();

  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: dir,
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const armOf = (name: string) => readFliesArmGuide(config, "paper").arms.find((a) => a.arm === name)!;

describe("what distinguishes an arm", () => {
  it("a setting most arms share is not a distinguisher", () => {
    // All three arms state the same entry_windows, so it separates none of them.
    for (const name of ["control", "bwb", "width-5"]) {
      const windows = armOf(name).overrides.find((o) => o.key === "entry_windows")!;
      expect(windows.sharedByMostArms).toBe(true);
    }
  });

  it("a setting only one arm states is", () => {
    const rule = armOf("bwb").overrides.find((o) => o.key === "center_rule")!;
    expect(rule.sharedByMostArms).toBe(false);
    expect(rule.inDefaults).toBe(false);
    expect(rule.value).toBe("gex");
  });

  it("a setting equal to the default is kept but flagged, never silently dropped", () => {
    // width-5 pinning wing_width to 5 when the default IS 5 is why that arm shows no width; the
    // row has to survive to say so, or the omission reads as a bug.
    const width = armOf("width-5").overrides.find((o) => o.key === "wing_width")!;
    expect(width.matchesDefault).toBe(true);
    expect(width.fallback).toBe(5);
  });

  it("centring follows the engine: center_rule if set, otherwise the arm's own name", () => {
    // The `gex` arm carries no center_rule key at all — a pure config diff reports nothing
    // separating it from control, when the centring rule IS the experiment.
    expect(armOf("bwb")).toMatchObject({ centring: "gex", centringFromName: false });
    expect(armOf("control")).toMatchObject({ centring: "atm", centringFromName: true });
  });

  it("the baseline has nothing left once shared and default settings are set aside", () => {
    const control = armOf("control");
    expect(control.overrides.filter((o) => !o.sharedByMostArms && !o.matchesDefault)).toHaveLength(0);
  });
});

describe("descriptions come from the module, not from the console", () => {
  it("reads the config's own notes, with the definition first", () => {
    const notes = armOf("control").notes;
    expect(notes.map((n) => n.key)).toEqual(["note", "history_note"]);
    expect(notes[0]!.text).toContain("baseline");
  });

  it("carries notes attached to the arms block as a whole", () => {
    expect(readFliesArmGuide(config, "paper").groupNotes.map((n) => n.key)).toEqual(["width_arms_note"]);
  });
});

describe("running versus finished", () => {
  it("an arm disabled with sessions in the book is retired, not broken", () => {
    expect(armOf("width-5")).toMatchObject({ enabled: false, retired: true, positions: 1 });
  });

  it("running arms come first, and carry their own ledger span", () => {
    const guide = readFliesArmGuide(config, "paper");
    expect(guide.arms.map((a) => a.arm)).toEqual(["control", "bwb", "width-5"]);
    expect(guide.arms[0]).toMatchObject({ firstSession: "2026-08-11", lastSession: "2026-08-13", positions: 2 });
    // Configured but never traded is its own state — neither running-with-history nor retired.
    expect(armOf("bwb")).toMatchObject({ enabled: true, retired: false, positions: 0, firstSession: null });
  });

  it("surfaces the module's own measurement breaks", () => {
    expect(readFliesArmGuide(config, "paper").breaks).toEqual([
      { date: "2026-08-09", scope: "*", kind: "cadence", reason: "tick cadence 60s -> 15s at the supervisor cutover" },
    ]);
  });
});

describe("a missing config", () => {
  it("says so rather than reporting no arms", () => {
    const gone: ConsoleConfig = { ...config, paths: { ...config.paths, fliesConfig: "/nope/flies.json" } };
    const guide = readFliesArmGuide(gone, "paper");
    expect(guide.configMissing).toBe(true);
    expect(guide.arms).toHaveLength(0);
  });
});
