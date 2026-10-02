import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { meicRiskConfigPath, type ConsoleConfig } from "../src/config.js";

/**
 * MEIC's arm registry left the repo on 2026-10-01: a machine's own registry is
 * `~/.cherrypick/config/meic.risk.json`, and MEIC runs the shipped control-only example until that
 * exists (`meic.paths.risk_profiles_path`). The console reads whichever MEIC is running from.
 */

function configWith(own: string, fallback?: string): ConsoleConfig {
  return { port: 0, paths: { meicRiskConfig: own, ...(fallback !== undefined ? { meicRiskConfigFallback: fallback } : {}) } } as unknown as ConsoleConfig;
}

describe("which MEIC registry the console reads", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meic-risk-"));
  const home = path.join(tmp, "config", "meic.risk.json");
  const example = path.join(tmp, "config.risk.example.json");
  fs.writeFileSync(example, JSON.stringify({ profiles: { control: { enabled: true } } }));

  it("the shipped example while this machine has no registry of its own", () => {
    expect(meicRiskConfigPath(configWith(home, example))).toBe(example);
  });

  it("the machine's own registry once it exists, even if it does not parse (MEIC reads it too)", () => {
    fs.mkdirSync(path.dirname(home), { recursive: true });
    fs.writeFileSync(home, "{ not json");
    expect(meicRiskConfigPath(configWith(home, example))).toBe(home);
    fs.rmSync(home);
  });

  it("an override with no fallback is read as given, as MEIC does", () => {
    expect(meicRiskConfigPath(configWith(path.join(tmp, "missing.json")))).toBe(path.join(tmp, "missing.json"));
  });
});
