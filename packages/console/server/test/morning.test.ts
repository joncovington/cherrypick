import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readMorning, listMorningSessions } from "../src/readers/overview.js";

/**
 * The morning reader passes the pack through; the failures this guards against are the quiet ones —
 * a null reading rendering as 0, a malformed pack taking the endpoint down, or a missing narrative
 * being indistinguishable from an empty one.
 */

let tmp: string;
let config: ConsoleConfig;

function writePack(session: string, doc: unknown): void {
  fs.writeFileSync(path.join(tmp, "overview", `morning-${session}.json`), JSON.stringify(doc));
}

function minimalPack(session: string): Record<string, unknown> {
  return {
    pack: "overview.morning",
    fact_version: 1,
    session,
    generated_at: `${session}T09:15:00-04:00`,
    readings: {
      spx: {
        value: 7798.99,
        basis: "live",
        session,
        as_of: `${session}T09:14:55-04:00`,
        source: "stream_cache:SPX",
        label: "S&P 500 (SPX)",
        prior_close: 7744.62,
        prior_change_pct: 0.7,
      },
      vix: { value: null, basis: null, session: null, as_of: null, source: "stream_cache:VIX", label: "VIX", prior_close: null, prior_change_pct: null },
    },
    levels: { symbol: "SPX", reference_price: 7798.99, reference_basis: "live", zero_gamma: 7750, call_wall: 7850, put_wall: 7700, net_gex: null, session, as_of: null, source: "gex.gex_regime_history" },
    sectors: {
      board: [
        { symbol: "XLK", sector: "Technology", change_pct: 1.2, close: 240.1, session },
        { symbol: "XLE", sector: "Energy", change_pct: null, close: null, session: null },
      ],
      strongest: { symbol: "XLK", sector: "Technology", change_pct: 1.2, close: 240.1, session },
      weakest: null,
      measured: 1,
    },
    gates: [
      { id: "vol_curve", label: "Vol curve", status: "met", value: 0.91, threshold: 1.0, detail: "VIX below VIX3M" },
      { id: "calm_tape", label: "Calm tape", status: "unknown", value: null, threshold: null, detail: "VVIX unmeasured" },
    ],
    phase: { phase: "yellow", reason: "one gate unmeasured", gates_total: 5, gates_measured: 4, gates_met: 3 },
    calendar: { is_fomc_day: false, next_fomc: "2026-09-16", fomc_year_known: true, is_triple_witching: false, is_quarterly_expiry: false, next_trading_day: "2026-08-18" },
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-morning-test-"));
  fs.mkdirSync(path.join(tmp, "overview"), { recursive: true });
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
      calendarsDir: path.join(tmp, "calendars"),
      pmccDir: path.join(tmp, "pmcc"),
      gexDir: path.join(tmp, "gex"),
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

describe("session resolution", () => {
  it("defaults to the latest session and lists them all in order", () => {
    writePack("2026-08-14", minimalPack("2026-08-14"));
    writePack("2026-08-17", minimalPack("2026-08-17"));
    writePack("2026-08-13", minimalPack("2026-08-13"));
    const payload = readMorning(config);
    expect(payload.sessions).toEqual(["2026-08-13", "2026-08-14", "2026-08-17"]);
    expect(payload.current?.session).toBe("2026-08-17");
  });

  it("serves an explicitly requested session", () => {
    writePack("2026-08-14", minimalPack("2026-08-14"));
    writePack("2026-08-17", minimalPack("2026-08-17"));
    expect(readMorning(config, "2026-08-14").current?.session).toBe("2026-08-14");
  });

  it("an empty or missing store is an empty list, not a throw", () => {
    expect(readMorning(config)).toEqual({ sessions: [], current: null, note: null });
    fs.rmSync(path.join(tmp, "overview"), { recursive: true });
    expect(listMorningSessions(config)).toEqual([]);
  });
});

describe("null is never zero", () => {
  it("an unmeasured reading passes through as null, and its neighbours stay measured", () => {
    writePack("2026-08-17", minimalPack("2026-08-17"));
    const current = readMorning(config).current;
    expect(current?.readings["vix"]).toMatchObject({ value: null, basis: null, priorClose: null });
    expect(current?.readings["spx"]).toMatchObject({ value: 7798.99, basis: "live", priorChangePct: 0.7 });
    expect(current?.levels).toMatchObject({ netGex: null, zeroGamma: 7750 });
    expect(current?.sectors?.board[1]).toMatchObject({ symbol: "XLE", changePct: null, close: null });
  });

  it("gate verdicts and the phase come from the pack, unfamiliar statuses read as unknown", () => {
    const pack = minimalPack("2026-08-17");
    (pack["gates"] as Record<string, unknown>[]).push({ id: "spot_vs_flip", label: "Spot vs flip", status: "surprise", value: 1, threshold: 0, detail: "" });
    writePack("2026-08-17", pack);
    const current = readMorning(config).current;
    expect(current?.phase).toMatchObject({ phase: "yellow", gatesMet: 3, gatesMeasured: 4 });
    expect(current?.gates.map((g) => g.status)).toEqual(["met", "unknown", "unknown"]);
  });
});

describe("the narrative", () => {
  it("is null when absent — distinct from an empty note", () => {
    writePack("2026-08-17", minimalPack("2026-08-17"));
    expect(readMorning(config).note).toBeNull();
  });

  it("is the raw markdown when present, for the requested session only", () => {
    writePack("2026-08-14", minimalPack("2026-08-14"));
    writePack("2026-08-17", minimalPack("2026-08-17"));
    fs.writeFileSync(path.join(tmp, "overview", "morning-2026-08-17.note.md"), "**Calm open.** Nothing armed.\n");
    expect(readMorning(config).note).toBe("**Calm open.** Nothing armed.\n");
    expect(readMorning(config, "2026-08-14").note).toBeNull();
  });
});

describe("malformed packs", () => {
  it("unparseable JSON yields current: null without throwing, and other sessions stay listed", () => {
    writePack("2026-08-14", minimalPack("2026-08-14"));
    fs.writeFileSync(path.join(tmp, "overview", "morning-2026-08-17.json"), "{not json");
    const payload = readMorning(config);
    expect(payload.sessions).toEqual(["2026-08-14", "2026-08-17"]);
    expect(payload.current).toBeNull();
    expect(readMorning(config, "2026-08-14").current?.session).toBe("2026-08-14");
  });

  it("a pack of the wrong shape degrades field by field, never takes the reader down", () => {
    writePack("2026-08-17", { readings: "nope", gates: "nope", phase: { phase: "plaid" }, sectors: 4 });
    const current = readMorning(config).current;
    expect(current).toMatchObject({ session: "2026-08-17", gates: [], phase: null });
    expect(current?.readings).toEqual({});
  });
});
