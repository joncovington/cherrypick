import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { suitePython } from "@console/shared/paths";
import type { ConsoleConfig } from "../src/config.js";
import {
  readFliesGateSessions,
  readFliesLiveGap,
  readFliesSelector,
  resetFliesEvidenceCache,
  setFliesEvidenceCaller,
} from "../src/services/fliesEvidenceBridge.js";

/**
 * The three flies evidence reads are the module's derivations (live-vs-paper matching, each gate
 * arm's era window from the ledger's breaks, the selector's departures and model intervals), so the
 * console asks for them rather than re-deriving them. What a TypeScript layer can still get wrong:
 * the argv (which ledger each verb reads), handing the module's numbers on unchanged, memoising the
 * poll, and -- the one that matters -- reporting an unavailable read as an error, never as empty.
 */

const config = { paths: { fliesDir: path.join("H", "data", "flies") } } as unknown as ConsoleConfig;

afterEach(() => {
  setFliesEvidenceCaller();
  resetFliesEvidenceCache();
});

describe("the flies evidence bridge", () => {
  it("asks each verb of the module and hands its numbers on unchanged", () => {
    const seen: string[] = [];
    setFliesEvidenceCaller((_c, verb) => {
      seen.push(verb);
      return {
        ok: true,
        json: { ok: true, ledger: "ok", arm: "control", sessions: [{ session: "2026-10-09", net_gap: -12.34 }], eras: {} },
        error: null,
      };
    });
    const out = readFliesLiveGap(config, 1_000);
    expect(out.ok).toBe(true);
    expect(out.data?.sessions[0]).toEqual({ session: "2026-10-09", net_gap: -12.34 });
    readFliesGateSessions(config, 1_000);
    readFliesSelector(config, 1_000);
    expect(seen).toEqual(["live-gap", "gate-sessions", "selector-sessions"]);
  });

  it("memoises a verb for the TTL, so a page poll is not a subprocess", () => {
    let calls = 0;
    setFliesEvidenceCaller(() => {
      calls += 1;
      return { ok: true, json: { ok: true, ledger: "ok", arms: [] }, error: null };
    });
    readFliesGateSessions(config, 1_000);
    readFliesGateSessions(config, 60_000);
    expect(calls).toBe(1);
    readFliesGateSessions(config, 200_000);
    expect(calls).toBe(2);
  });

  it("reports an unavailable module as an error, never as an empty result", () => {
    setFliesEvidenceCaller(() => ({ ok: false, json: null, error: "flies evidence unavailable — exit 1" }));
    const out = readFliesSelector(config, 1_000);
    expect(out.ok).toBe(false);
    expect(out.data).toBeNull();
    expect(out.error).toContain("unavailable");
  });

  it("reports a refusal from the module as an error", () => {
    setFliesEvidenceCaller(() => ({ ok: true, json: { ok: false, error: "no such table" }, error: null }));
    expect(readFliesLiveGap(config, 1_000)).toEqual({ ok: false, data: null, error: "no such table" });
  });
});

/** The real verbs against an empty home: proof the argv parses and the module answers. Skipped
 * visibly when the suite's Python cannot import the flies package. */
const python = suitePython();
const hasFlies = spawnSync(python, ["-c", "import cherrypick.flies.cli"], { encoding: "utf-8" }).status === 0;

describe.skipIf(!hasFlies)("the real flies verbs", () => {
  it("answer for an absent ledger without failing or creating one", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flies-evidence-"));
    const cfg = { paths: { fliesDir: dir } } as unknown as ConsoleConfig;
    expect(readFliesLiveGap(cfg).data?.ledger).toBe("absent");
    expect(readFliesGateSessions(cfg).data?.ledger).toBe("absent");
    expect(readFliesSelector(cfg).data?.ledger).toBe("absent");
    expect(fs.readdirSync(dir)).toEqual([]);
  });
});
