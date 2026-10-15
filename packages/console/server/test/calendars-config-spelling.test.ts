import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { loadParams } from "../src/readers/calendars.js";

/**
 * The Calendars page's arm list reads the module's registry under either spelling. The real
 * config says `books` (a spelling `cherrypick.core.config` resolves for good), and the reader read
 * only `arms`, so the page listed no arms at all until 2026-10-15.
 */

function configWith(doc: unknown): ConsoleConfig {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-cal-spelling-"));
  const file = path.join(tmp, "calendars.json");
  fs.writeFileSync(file, JSON.stringify(doc));
  return { paths: { calendarsConfigCandidates: [file], calendarsDir: tmp } } as unknown as ConsoleConfig;
}

describe("the calendars arm registry, under either spelling", () => {
  it("reads `books`", () => {
    const p = loadParams(configWith({ books: { control: { enabled: true }, path: { enabled: true } } }));
    expect(p.arms.map((a) => a.name).sort()).toEqual(["control", "path"]);
  });

  it("reads `arms`", () => {
    const p = loadParams(configWith({ arms: { control: { enabled: true } } }));
    expect(p.arms.map((a) => a.name)).toEqual(["control"]);
  });
});
