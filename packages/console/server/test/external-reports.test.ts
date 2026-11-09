import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { REPO_ROOT } from "../src/config.js";
import { MAX_REPORT_BYTES, readExternalReports, validateReport } from "../src/readers/externalReports.js";

/**
 * External reports: `data/external/*.json`, judged against the one schema file the Python validator
 * also reads. The failures guarded: a bad file half-drawn, a stale report passing as fresh, and the
 * two validators drifting apart (they share `external_report_cases.json`).
 */

const NOW = Date.parse("2026-11-09T22:00:00Z");
let tmp: string;
let config: ConsoleConfig;

function good(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 1, id: "demo", title: "Demo", source: "my script", as_of: "2026-11-09T16:30:00-05:00",
    stale_after_hours: 24, summary: "one line",
    sections: [
      { heading: "Table", table: { columns: ["a", "b"], rows: [["x", 1], ["y", null]] } },
      { heading: "Notes", text: "plain words" },
    ],
    ...over,
  };
}

function write(name: string, doc: unknown): void {
  fs.writeFileSync(path.join(tmp, "data", "external", `${name}.json`), typeof doc === "string" ? doc : JSON.stringify(doc));
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "external-"));
  fs.mkdirSync(path.join(tmp, "data", "external"), { recursive: true });
  config = { port: 0, paths: { cherrypick: tmp } } as unknown as ConsoleConfig;
});

describe("the reader", () => {
  it("returns a valid report with its age, fresh", () => {
    write("demo", good());
    const out = readExternalReports(config, NOW);
    expect(out.invalid).toEqual([]);
    expect(out.reports).toHaveLength(1);
    expect(out.reports[0]).toMatchObject({ id: "demo", title: "Demo", source: "my script", summary: "one line", stale: false });
    expect(out.reports[0]!.ageHours).toBeCloseTo(0.5, 1);
  });

  it("flags a report older than its window as stale, still valid", () => {
    write("demo", good());
    const out = readExternalReports(config, NOW + 25 * 3_600_000);
    expect(out.reports[0]).toMatchObject({ stale: true });
    expect(out.invalid).toEqual([]);
  });

  it("an invalid file comes back as reasons and none of its content", () => {
    write("bad", good({ id: "bad", title: "", stale_after_hours: 0 }));
    write("ok", good({ id: "ok" }));
    const out = readExternalReports(config, NOW);
    expect(out.reports.map((r) => r.id)).toEqual(["ok"]);
    expect(out.invalid).toHaveLength(1);
    expect(out.invalid[0]!.id).toBe("bad");
    expect(out.invalid[0]!.reasons.length).toBeGreaterThanOrEqual(2);
    expect(JSON.stringify(out.invalid)).not.toContain("plain words");
  });

  it("a file that is not JSON, an id that is not the file name, and an oversize file are reasons not crashes", () => {
    write("broken", "{oops");
    write("renamed", good({ id: "other" }));
    write("huge", JSON.stringify({ pad: "x".repeat(MAX_REPORT_BYTES) }));
    const by = Object.fromEntries(readExternalReports(config, NOW).invalid.map((r) => [r.id, r.reasons.join(" ")]));
    expect(by["broken"]).toContain("not readable JSON");
    expect(by["renamed"]).toContain("does not match the file name");
    expect(by["huge"]).toContain("byte limit");
  });

  it("a missing directory is an empty answer that says so", () => {
    fs.rmSync(path.join(tmp, "data", "external"), { recursive: true });
    expect(readExternalReports(config, NOW)).toEqual({ reports: [], invalid: [], directoryExists: false });
  });

  it("an empty directory exists with nothing in it", () => {
    expect(readExternalReports(config, NOW)).toEqual({ reports: [], invalid: [], directoryExists: true });
  });

  it("ignores files that are not .json", () => {
    fs.writeFileSync(path.join(tmp, "data", "external", "notes.txt"), "hello");
    expect(readExternalReports(config, NOW).invalid).toEqual([]);
  });
});

describe("the validator and the Python one agree", () => {
  const cases = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "packages", "core", "tests", "fixtures", "external_report_cases.json"), "utf8"),
  ).cases as Array<{ name: string; stem: string; valid: boolean; doc: unknown }>;
  for (const c of cases) {
    it(c.name, () => {
      expect(validateReport(c.doc, c.stem).length === 0).toBe(c.valid);
    });
  }
});
