import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { suitePython } from "@console/shared";
import type { ConsoleConfig } from "../src/config.js";
import { spawnModuleCli } from "../src/services/moduleCli.js";
import {
  readBwbLiveGap,
  readBwbVol,
  resetBwbEvidenceCache,
  setBwbEvidenceCaller,
} from "../src/services/bwbEvidenceBridge.js";
import {
  readImpliedVarChecks,
  readQuoteHistoryCoverage,
  resetDataEvidenceCache,
  setDataEvidenceCaller,
} from "../src/services/dataEvidenceBridge.js";

/**
 * bwb's live-vs-paper and entry-vol reads, the quote-history coverage and the implied-variance check
 * are all the modules' own (bridged, never derived here). What these pin is the TypeScript layer:
 * which verb is asked, that the numbers pass through unchanged, that a module's refusal-as-reading
 * (`not_bracketed`) is data while an unreadable store is an error, and that one failing read on the
 * vol page names itself without blanking the other two.
 */

const config = {
  paths: { bwbDir: path.join("H", "bwb"), gexDir: path.join("H", "gex"), streamCacheDb: path.join("H", "c.db") },
} as unknown as ConsoleConfig;

afterEach(() => {
  setBwbEvidenceCaller();
  resetBwbEvidenceCache();
  setDataEvidenceCaller();
  resetDataEvidenceCache();
});

describe("the bwb evidence bridge", () => {
  it("passes live-gap through, refusals named by the module", () => {
    setBwbEvidenceCaller((_c, verb) => ({
      ok: true,
      json: {
        ok: true,
        ledger: "ok",
        arm: verb === "live-gap" ? "bounce" : null,
        sessions: [],
        refusals: { regime_inverted: { attempts: 3, sessions: 1, first: "2026-10-13", last: "2026-10-13", gate: "live.vix9d_vix_max" } },
      },
      error: null,
    }));
    const out = readBwbLiveGap(config, 1_000);
    expect(out.ok).toBe(true);
    expect(out.data?.refusals["regime_inverted"]?.gate).toBe("live.vix9d_vix_max");
  });

  it("keeps each vol read's failure to itself", () => {
    setBwbEvidenceCaller((_c, verb) =>
      verb === "iv-premium"
        ? { ok: false, json: null, error: "bwb evidence unavailable — exit 1" }
        : { ok: true, json: { ok: true, ledger: "ok", positions: [], arms: {} }, error: null },
    );
    const vol = readBwbVol(config, 1_000);
    expect(vol.entryContext.ok).toBe(true);
    expect(vol.regimeSplit.ok).toBe(true);
    expect(vol.ivPremium).toEqual({ ok: false, data: null, error: "bwb evidence unavailable — exit 1" });
  });
});

describe("the System › data bridge", () => {
  it("treats an impliedvar refusal as the reading and an unreadable cache as an error", () => {
    setDataEvidenceCaller((_c, req) => {
      if (req.kind !== "impliedvar") return { ok: true, json: { ok: true, symbols: {} }, error: null };
      return req.index === "VIX"
        ? { ok: true, json: { ok: false, index: "VIX", reason: "not_bracketed", offered: [["2026-10-16", 4]] }, error: null }
        : { ok: true, json: { ok: false, error: "OperationalError: unable to open database file" }, error: null };
    });
    const { checks } = readImpliedVarChecks(config, 1_000);
    expect(checks["VIX"]?.ok).toBe(true);
    expect(checks["VIX"]?.data?.reason).toBe("not_bracketed");
    expect(checks["VIX9D"]?.ok).toBe(false);
    expect(checks["VIX9D"]?.error).toContain("unable to open");
  });

  it("asks quote-history coverage of core and memoises it", () => {
    let calls = 0;
    setDataEvidenceCaller(() => {
      calls += 1;
      return { ok: true, json: { ok: true, exists: true, recorded_from: "2026-10-13", symbols: {} }, error: null };
    });
    expect(readQuoteHistoryCoverage(config, 1_000).data?.recorded_from).toBe("2026-10-13");
    readQuoteHistoryCoverage(config, 100_000);
    expect(calls).toBe(1);
  });
});

const python = suitePython();
const canRun = spawnSync(python, ["-c", "import cherrypick.core"], { encoding: "utf-8" }).status === 0;

describe.skipIf(!canRun)("spawnModuleCli's exit codes", () => {
  it("reads stdout on a declared exit code and fails on any other", () => {
    const argv = ["-c", "import json,sys; print(json.dumps({'ok': False, 'reason': 'x'})); sys.exit(1)"];
    expect(spawnModuleCli(argv, "u").ok).toBe(false);
    const ok = spawnModuleCli(argv, "u", [0, 1]);
    expect(ok.ok).toBe(true);
    expect(ok.json?.["reason"]).toBe("x");
  });
});

describe.skipIf(!canRun)("the real core verbs", () => {
  it("quote-history coverage answers for an absent store without creating it", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qh-"));
    const cfg = { paths: { gexDir: dir, streamCacheDb: path.join(dir, "c.db") } } as unknown as ConsoleConfig;
    const out = readQuoteHistoryCoverage(cfg);
    expect(out.ok, out.error ?? "").toBe(true);
    expect(out.data?.exists).toBe(false);
    expect(fs.readdirSync(dir)).toEqual([]);
  });
});
