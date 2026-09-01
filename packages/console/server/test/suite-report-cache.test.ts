import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { buildSuiteReport } from "../src/services/report.js";

/**
 * The suite report is memoised, and the case that memoisation must not break is the one the review
 * exists to make: a session is PROVISIONAL before it is FINAL, and `review-final` rewrites the same
 * `eod-<session>.json` the next morning rather than adding a file.
 *
 * A cache keyed on the review directory would miss that rewrite entirely — a directory's timestamp
 * moves when an entry is added or removed, not when one is edited — and the page would keep serving
 * provisional numbers as settled ones.
 */

function configFor(tmp: string): ConsoleConfig {
  return {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
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
}

function writeFactSet(dir: string, session: string, net: number): void {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, `eod-${session}.json`),
    JSON.stringify({
      modules: { meic: { ok: true, results: { net, closed: 1, wins: 1, losses: 0 } } },
    }),
  );
}

describe("the suite report's cache", () => {
  it("picks up a provisional session being restated as final", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);
    const reviewDir = config.paths.reviewDir;

    writeFactSet(reviewDir, "2026-08-19", 100);
    expect(buildSuiteReport(config).suite.net).toBe(100);

    // Same path, new contents — exactly what review-final does to review-provisional's artifact.
    // The restatement lands the NEXT MORNING, so the timestamp is advanced to model that gap
    // rather than rewriting inside the same millisecond, which no scheduled pair of jobs does.
    writeFactSet(reviewDir, "2026-08-19", 250);
    const restated = path.join(reviewDir, "eod-2026-08-19.json");
    const nextMorning = new Date(Date.now() + 18 * 3600 * 1000);
    fs.utimesSync(restated, nextMorning, nextMorning);

    expect(buildSuiteReport(config).suite.net).toBe(250);
  });

  it("picks up a newly added session", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);

    writeFactSet(config.paths.reviewDir, "2026-08-19", 100);
    expect(buildSuiteReport(config).suite.net).toBe(100);

    writeFactSet(config.paths.reviewDir, "2026-08-20", 40);

    const after = buildSuiteReport(config);
    expect(after.suite.net).toBe(140);
    expect(after.daily.map((d) => d.session)).toEqual(["2026-08-19", "2026-08-20"]);
  });

  it("serves a repeated call without the fact sets changing", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);
    writeFactSet(config.paths.reviewDir, "2026-08-19", 100);

    expect(buildSuiteReport(config)).toEqual(buildSuiteReport(config));
  });
});
