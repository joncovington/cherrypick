import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * No request path waits on python synchronously. A `spawnSync` holds every request and the
 * heartbeat for as long as the subprocess runs: on 2026-11-02 System › data froze the console for
 * 41 s and the calendars table for 17.6 s, and the GEX panel's equivalent got it restarted. Bridges
 * spawn through `spawnModuleCliAsync` and memoise with `AsyncMemo`.
 *
 * The exceptions, each a deliberate one-off rather than a poll:
 */
const ALLOWED: Record<string, string> = {
  // the spawners themselves
  "services/moduleCli.ts": "defines spawnModuleCli and spawnModuleCliAsync",
  // the credential scope probe, once per process at startup
  "auth/suiteBridge.ts": "startup credential probe",
  // the advisor page's two buttons (kill, dismiss), a POST on a click
  "services/advisorBridge.ts": "button POSTs",
  // the sync twins of readAnalysisAsync / readModuleMetricsAsync, kept for their tests; no reader calls them
  "services/analysisBridge.ts": "sync twin definition",
  "services/metricsBridge.ts": "sync twin definition",
};

const SYNC_CALL = /\b(spawnSync|execFileSync|execSync|spawnModuleCli|readModuleMetrics|readAnalysis)\s*\(/;

const src = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : p.endsWith(".ts") ? [p] : [];
  });
}

describe("no request path spawns python synchronously", () => {
  it("leaves the synchronous spawners to the named one-offs", () => {
    const offenders: string[] = [];
    for (const file of walk(src)) {
      const rel = path.relative(src, file).split(path.sep).join("/");
      if (rel in ALLOWED) continue;
      fs.readFileSync(file, "utf-8")
        .split(/\r?\n/)
        .forEach((line, i) => {
          const code = line.replace(/\/\/.*$/, "");
          if (SYNC_CALL.test(code) && !/^\s*\*/.test(code)) offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });
});
