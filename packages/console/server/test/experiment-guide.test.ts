import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesArmGuide, readMeicProfileGuide } from "../src/readers/experimentGuide.js";

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
  advice: { enabled: true, base_arm: "control" },
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
  pos.run("advised:control", "2026-08-13");
  pos.run("advised:iron", "2026-08-01");
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
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const armOf = (name: string) => readFliesArmGuide(config, "paper").entries.find((e) => e.name === name)!;

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
    const centringOf = (name: string) => armOf(name).derived.find((d) => d.label === "centres on")!;
    expect(centringOf("bwb")).toMatchObject({ value: "GEX", detail: "set by center_rule" });
    expect(centringOf("control").value).toBe("ATM");
    expect(centringOf("control").detail).toContain("own name");
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
    expect(guide.entries.map((e) => e.name)).toEqual(["control", "bwb", "advised:control", "width-5", "advised:iron"]);
    expect(guide.entries[0]).toMatchObject({ firstSession: "2026-08-11", lastSession: "2026-08-13", positions: 2 });
    // Configured but never traded is its own state — neither running-with-history nor retired.
    expect(armOf("bwb")).toMatchObject({ enabled: true, retired: false, positions: 0, firstSession: null });
  });

  it("the advice block's current book runs; a book it no longer produces is retired, not gone", () => {
    // advised:* books never appear in the arm registry — the paper loop synthesizes them from the
    // advice block — so registry absence must not read as "gone from config" while one is trading.
    expect(armOf("advised:control")).toMatchObject({ enabled: true, retired: false, removed: false, positions: 1 });
    expect(armOf("advised:iron")).toMatchObject({ enabled: false, retired: true, removed: false });
    expect(armOf("advised:control").derived[0]).toMatchObject({ label: "advised twin of", value: "control" });
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
    expect(guide.entries).toHaveLength(0);
  });
});

/**
 * MEIC's shape differs from flies' in two ways that matter: a profile states every parameter rather
 * than overriding a `defaults` block (so the majority rule does the work), and its book carries
 * profiles the config no longer defines at all.
 */
describe("MEIC risk profiles", () => {
  let meicConfig: ConsoleConfig;

  beforeAll(() => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicguide-"));
    fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
    fs.writeFileSync(
      path.join(tmp, "config", "meic.json"),
      JSON.stringify({
        min_iv_rank: 0.3,
        stop_trigger_ratio: 0.95,
        advice: { enabled: true, base_profile: "open" },
      }),
    );
    fs.writeFileSync(
      path.join(tmp, "config.risk.json"),
      JSON.stringify({
        _description: "Parallel arms of one experiment, not a ladder.",
        active_profile: "control",
        profiles: {
          control: { enabled: true, min_iv_rank: 0.3, stop_trigger_ratio: 0.95, _note: "The baseline." },
          open: { enabled: true, min_iv_rank: 0.0, stop_trigger_ratio: 0.95, _note: "Removes the IV floor." },
          sign: { enabled: true, min_iv_rank: 0.3, stop_trigger_ratio: 0.95, _note: "Same gates as control." },
        },
      }),
    );
    const dir = path.join(tmp, "meic");
    fs.mkdirSync(dir, { recursive: true });
    const db = new Database(path.join(dir, "paper_trades.db"));
    db.exec(`
      CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, risk_profile TEXT, trade_date TEXT);
      CREATE TABLE measurement_breaks (
        id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, reason TEXT, detail TEXT, created_at TEXT
      );
    `);
    const ins = db.prepare("INSERT INTO ic_trades (risk_profile, trade_date) VALUES (?, ?)");
    ins.run("control", "2026-08-12");
    ins.run("open", "2026-08-13");
    ins.run("large-spx", "2026-07-13");
    ins.run("advised:open", "2026-08-14");
    ins.run("advised:control", "2026-08-13");
    db.close();

    meicConfig = { ...config, paths: { ...config.paths, meicDir: dir, cherrypick: tmp, meicRiskConfig: path.join(tmp, "config.risk.json") } };
  });

  const guide = () => readMeicProfileGuide(meicConfig, "paper");

  it("distinguishes on the value the profile does not share with its siblings", () => {
    const open = guide().entries.find((e) => e.name === "open")!;
    const distinguishing = open.overrides.filter((o) => !o.sharedByMostArms && !o.matchesDefault);
    expect(distinguishing.map((o) => o.key)).toEqual(["min_iv_rank"]);
    // The base value comes from the module's own config, since risk profiles have no defaults block.
    expect(distinguishing[0]!.fallback).toBe(0.3);
  });

  it("a profile matching the base and its siblings has nothing distinguishing", () => {
    const control = guide().entries.find((e) => e.name === "control")!;
    expect(control.overrides.filter((o) => !o.sharedByMostArms && !o.matchesDefault)).toHaveLength(0);
  });

  it("lists profiles the book still holds but the config has dropped", () => {
    const gone = guide().entries.find((e) => e.name === "large-spx")!;
    expect(gone).toMatchObject({ removed: true, positions: 1, enabled: false });
    // Nothing to describe it with — that is the point of flagging it rather than omitting it.
    expect(gone.notes).toHaveLength(0);
  });

  it("advised books follow the advice block, not the profile registry", () => {
    // advice.base_profile is `open`, so advised:open is the running book; advised:control stopped
    // being produced when the base was re-pointed, so it is retired — but never "gone from config",
    // because no advised book was ever IN the profile registry to be gone from.
    expect(guide().entries.find((e) => e.name === "advised:open")!).toMatchObject({
      enabled: true,
      retired: false,
      removed: false,
    });
    expect(guide().entries.find((e) => e.name === "advised:control")!).toMatchObject({
      enabled: false,
      retired: true,
      removed: false,
    });
  });

  it("takes the set-level notes from the config root", () => {
    expect(guide().groupNotes.map((n) => n.key)).toContain("description");
    expect(guide().unit).toBe("risk profile");
  });
});
