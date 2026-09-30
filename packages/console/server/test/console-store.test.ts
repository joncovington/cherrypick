import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { getPrefs, setPref } from "../src/store/consoleDb.js";

/**
 * The console's own store kept ten tables from the retired research surfaces (and ~60 MB of
 * candles) long after anything read them. Opening it now drops them -- and must never take a
 * preference with them, since a preference is the one thing this file is for.
 */
describe("the console's own store", () => {
  it("drops the retired tables on open and keeps every preference", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-store-"));
    const pre = new Database(path.join(dir, "console.db"));
    pre.exec(`CREATE TABLE candles (symbol TEXT, ts INTEGER, close REAL);
              CREATE TABLE staged_orders (id TEXT);
              CREATE TABLE console_prefs (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);`);
    const ins = pre.prepare("INSERT INTO candles VALUES (?, ?, ?)");
    for (let i = 0; i < 500; i++) ins.run("SPY", i, 500 + i);
    pre.prepare("INSERT INTO console_prefs VALUES (?, ?, ?)").run("mode", JSON.stringify("live"), "2026-09-01");
    pre.close();

    const config = { paths: { consoleData: dir } } as unknown as ConsoleConfig;
    expect(getPrefs(config)).toEqual({ mode: "live" }); // opens -> migrates
    setPref(config, "theme", "dark");
    expect(getPrefs(config)).toEqual({ mode: "live", theme: "dark" });

    const after = new Database(path.join(dir, "console.db"), { readonly: true });
    const tables = after
      .prepare<[], { name: string }>("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
      .all()
      .map((r) => r.name);
    after.close();
    expect(tables).toEqual(["console_prefs"]);
  });
});
