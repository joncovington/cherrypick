import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readRetiredArms } from "../src/readers/armStatus.js";

/**
 * The arm filters hide what a module's own config retires. Each module spells its registry its own
 * way (`arms`, `books`, `profiles`), and a missing file must hide nothing rather than fail.
 */

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-armstatus-"));
  const cfgDir = path.join(tmp, "config");
  fs.mkdirSync(cfgDir, { recursive: true });
  const write = (name: string, doc: unknown) => fs.writeFileSync(path.join(cfgDir, name), JSON.stringify(doc));
  write("flies.json", { arms: { control: {}, callwall: { enabled: false }, "range-high": { enabled: true } } });
  write("meic.risk.json", {
    profiles: { control: { enabled: true }, "live-minus-otmfloor": { enabled: false }, "gex-open": { enabled: false } },
  });
  write("bwb.json", { books: { control: {}, wall: { enabled: false } } });
  write("curve.json", { books: { control: {}, hook: { enabled: false, _note: "retired" } } });
  fs.writeFileSync(path.join(cfgDir, "pmcc.json"), "{ not json");
  config = {
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
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(cfgDir, "meic.risk.json"),
      fliesConfig: path.join(cfgDir, "flies.json"),
    },
  };
});

describe("retired arms, from each module's own config", () => {
  it("lists exactly the arms declared enabled: false, whatever the registry is called", () => {
    const { retired } = readRetiredArms(config);
    expect(retired.flies).toEqual(["callwall"]);
    expect(retired.meic).toEqual(["gex-open", "live-minus-otmfloor"]);
    expect(retired.bwb).toEqual(["wall"]);
    expect(retired.curve).toEqual(["hook"]);
  });

  it("hides nothing for a module whose config is missing or unreadable", () => {
    const { retired } = readRetiredArms(config);
    expect(retired.pmcc).toEqual([]);
    expect(retired.contango).toEqual([]);
  });
});
