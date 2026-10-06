/**
 * What the modules declare, read by something. Two hand-kept lists drifted on 2026-10-05: contango
 * shipped with no nav link, and the web's attempts list was a module behind the server's. These
 * checks are driven off what the system itself declares, not off another list:
 *
 * - every `*_decisions` / `*_entry_attempts` table a module's ledger schema declares (`db.py`) is
 *   named by some console reader -- a journal the module writes and nothing reads is a page gap;
 * - every module log the orchestrator's example config declares (`modules.<m>.paper.log`) is a
 *   source in `readers/logs.ts`.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const PACKAGES = path.resolve(__dirname, "..", "..", "..");
const SERVER_SRC = path.resolve(__dirname, "..", "src");

function walk(dir: string, match: (f: string) => boolean, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "__pycache__" || entry.name === "tests") continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p, match, out);
    else if (match(p)) out.push(p);
  }
  return out;
}

const readerText = walk(SERVER_SRC, (f) => f.endsWith(".ts"))
  .map((f) => fs.readFileSync(f, "utf8"))
  .join("\n");

function declaredTables(): Array<{ table: string; file: string }> {
  const out: Array<{ table: string; file: string }> = [];
  for (const pkg of fs.readdirSync(PACKAGES)) {
    if (pkg === "console" || pkg === "core") continue;
    const root = path.join(PACKAGES, pkg);
    if (!fs.statSync(root).isDirectory()) continue;
    for (const file of walk(root, (f) => path.basename(f) === "db.py")) {
      const text = fs.readFileSync(file, "utf8");
      for (const m of text.matchAll(/CREATE TABLE IF NOT EXISTS (\w*(?:decisions|entry_attempts))\b/g)) {
        out.push({ table: m[1]!, file: path.relative(PACKAGES, file) });
      }
    }
  }
  return out;
}

describe("module tables and logs the console reads", () => {
  const tables = declaredTables();

  it("finds the journals it is checking (a check over nothing would pass)", () => {
    expect(tables.length).toBeGreaterThanOrEqual(10);
  });

  it.each(tables.map((t) => [t.table, t.file]))("%s (%s) is read by a console reader", (table) => {
    expect(new RegExp(`\\b${table}\\b`).test(readerText)).toBe(true);
  });

  it("lists every module log the orchestrator declares", () => {
    const example = JSON.parse(fs.readFileSync(path.join(PACKAGES, "orchestrator", "config.example.json"), "utf8")) as {
      modules: Record<string, { paper?: { log?: string } }>;
    };
    const logs = fs.readFileSync(path.join(SERVER_SRC, "readers", "logs.ts"), "utf8");
    const missing = Object.entries(example.modules)
      .filter(([, m]) => typeof m.paper?.log === "string")
      .filter(([, m]) => !logs.includes(m.paper!.log!))
      .map(([name, m]) => `${name}: ${m.paper!.log!}`);
    expect(missing).toEqual([]);
  });
});
