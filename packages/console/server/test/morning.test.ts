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
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
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
    expect(readMorning(config)).toEqual({ sessions: [], current: null, note: null, technicals: null });
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

describe("the deployment block", () => {
  function withDeployment(session: string, deployment: unknown): Record<string, unknown> {
    return { ...minimalPack(session), fact_version: 2, deployment };
  }

  const fullBlock = {
    signals: [
      { id: "vix_level", label: "VIX percentile", status: "measured", score: 82.4, value: 14.2, weight: 0.25, detail: "14.2 at the 18th percentile" },
      { id: "credit", label: "Credit proxy", status: "unknown", score: null, value: null, weight: 0.15, detail: "too little history" },
    ],
    signals_measured: 4,
    signals_total: 5,
    weights_renormalized: true,
    deferred: ["factor_crowding"],
    record_only: true,
    note: "a recorded measurement -- feeds no gate, no phase, no sizing",
    score: 71.3,
    zone: "full",
    reason: null,
  };

  it("passes the score, zone and signals through untouched", () => {
    writePack("2026-08-17", withDeployment("2026-08-17", fullBlock));
    const d = readMorning(config).current?.deployment;
    expect(d).toMatchObject({ score: 71.3, zone: "full", signalsMeasured: 4, weightsRenormalized: true });
    expect(d?.deferred).toEqual(["factor_crowding"]);
    expect(d?.signals[0]).toMatchObject({ id: "vix_level", status: "measured", score: 82.4, weight: 0.25 });
  });

  it("an unmeasured signal keeps a null score — never a zero contribution", () => {
    writePack("2026-08-17", withDeployment("2026-08-17", fullBlock));
    const credit = readMorning(config).current?.deployment?.signals[1];
    expect(credit).toMatchObject({ id: "credit", status: "unknown", score: null, value: null });
  });

  it("an unfamiliar signal status reads as unknown, never as measured", () => {
    const block = { ...fullBlock, signals: [{ id: "vix_level", label: "VIX", status: "probably", score: 90, value: 1, weight: 0.25, detail: "" }] };
    writePack("2026-08-17", withDeployment("2026-08-17", block));
    expect(readMorning(config).current?.deployment?.signals[0].status).toBe("unknown");
  });

  it("an unfamiliar zone is no zone at all, never a guess", () => {
    writePack("2026-08-17", withDeployment("2026-08-17", { ...fullBlock, zone: "aggressive" }));
    expect(readMorning(config).current?.deployment?.zone).toBeNull();
  });

  it("a scoreless block carries its reason instead of a number", () => {
    const block = { ...fullBlock, score: null, zone: null, reason: "only 2 of 5 signals measured" };
    writePack("2026-08-17", withDeployment("2026-08-17", block));
    const d = readMorning(config).current?.deployment;
    expect(d).toMatchObject({ score: null, zone: null, reason: "only 2 of 5 signals measured" });
  });

  it("a pre-v2 pack has no deployment block at all — null, not an empty one", () => {
    // The page omits the card entirely on these; an empty card would imply a score of nothing.
    writePack("2026-08-17", minimalPack("2026-08-17"));
    expect(readMorning(config).current?.deployment).toBeNull();
  });

  it("a malformed block degrades to nulls rather than taking the reader down", () => {
    writePack("2026-08-17", withDeployment("2026-08-17", { signals: "nope", score: "high" }));
    const d = readMorning(config).current?.deployment;
    expect(d).toMatchObject({ score: null, zone: null, signals: [] });
    expect(d?.deferred).toEqual([]);
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

describe("fact version 4 blocks", () => {
  function v4(session: string): Record<string, unknown> {
    const pack = minimalPack(session);
    pack["fact_version"] = 4;
    pack["premarket"] = {
      futures: {
        es: {
          value: 7774.25, basis: "prior", session: "2026-09-27", as_of: null, source: "stream_cache:/ESZ26:XCME",
          label: "S&P 500 e-mini (/ES)", prior_close: 7774.25, prior_change_pct: -0.4, product: "ES",
          prior_settle: 7805.75, prior_settle_session: "2026-09-25", change_vs_prior_close_pct: null, change_reason: "no_live_print",
        },
      },
      indexes: { ndx: { value: 30608.13, basis: "prior", session: "2026-09-25", label: "Nasdaq-100 (NDX)" } },
      measured_futures: 0,
      record_only: true,
    };
    pack["moves"] = {
      weekly_expected_move: { pct: 2.06, points: 159.5, vix: 14.87, basis: "prior", reason: null },
      realized_vol: { pct: 12.11, sessions: 10, through: "2026-09-24", reason: null },
      vix_minus_realized: 2.76,
    };
    pack["yields"] = {
      session: "2026-09-25", source: "treasury_par_curve", yields: { "2y": 4.81, "10y": 5.17 },
      change_bp: { "2y": -6, "10y": null }, spread_2s10s_bp: 36, spread_3m10y_bp: 93, reason: null,
    };
    pack["vol_regime"] = {
      curve: [], slope: {}, percentiles: { vxn: { value: 20.87, samples: 252, percentile: 23, reason: null, source: "cboe_file_prior_close" } },
      risk_reversal_25d_30d: { session: "2026-09-25", target_dte: 30, call_25d_iv_pct: 11.18, put_25d_iv_pct: 14.67, rr_vol_pts: -3.49, expirations: [{ expiration: "2026-10-23", dte: 28 }], source: "cboe_delayed_spx_chain" },
    };
    (pack["calendar"] as Record<string, unknown>)["releases"] = [
      { name: "Gross Domestic Product", date: "2026-09-30", time_et: "08:30", source: "BEA", today: false },
      { name: "Employment Situation", date: "2026-10-02", time_et: null, source: "FRED", today: false },
    ];
    (pack["calendar"] as Record<string, unknown>)["earnings"] = {
      rows: [
        { symbol: "MU", date: "2026-09-30", when: "After market close", expiration: "2026-10-02", spot: 1082.28, strike: 1082.5, straddle: 93.38, expected_move: 79.37, expected_move_pct: 7.33, reason: null },
        { symbol: "X", date: "2026-10-01", when: null, expiration: "2026-10-02", spot: 10, strike: null, expected_move: null, expected_move_pct: null, reason: "straddle not two-sided" },
      ],
      as_of: "2026-09-27T22:40:00+00:00",
      reason: null,
    };
    return pack;
  }

  it("a future with no live print keeps a null change and says why — never 0.00%", () => {
    writePack("2026-09-28", v4("2026-09-28"));
    const es = readMorning(config).current?.premarket?.futures["es"];
    expect(es).toMatchObject({ value: 7774.25, priorSettle: 7805.75, changeVsPriorClosePct: null, changeReason: "no_live_print" });
  });

  it("moves, yields, the risk reversal and a percentile's source pass through", () => {
    writePack("2026-09-28", v4("2026-09-28"));
    const c = readMorning(config).current;
    expect(c?.moves?.weeklyExpectedMove).toMatchObject({ pct: 2.06, points: 159.5 });
    expect(c?.yields?.changeBp).toEqual({ "2y": -6, "10y": null });
    expect(c?.volRegime?.riskReversal).toMatchObject({ rrVolPts: -3.49, expirations: [{ expiration: "2026-10-23", dte: 28 }] });
    expect(c?.volRegime?.percentiles["vxn"]?.source).toBe("cboe_file_prior_close");
  });

  it("an unpriced earnings row is kept with its reason, and an untimed release stays untimed", () => {
    writePack("2026-09-28", v4("2026-09-28"));
    const cal = readMorning(config).current?.calendar;
    expect(cal?.earnings?.rows.map((r) => [r.symbol, r.expectedMove, r.reason])).toEqual([
      ["MU", 79.37, null],
      ["X", null, "straddle not two-sided"],
    ]);
    expect(cal?.releases?.[1]).toMatchObject({ name: "Employment Situation", timeEt: null });
  });

  it("an older pack has none of them — null, not empty blocks that would read as measured", () => {
    writePack("2026-08-17", minimalPack("2026-08-17"));
    const c = readMorning(config).current;
    expect(c).toMatchObject({ premarket: null, moves: null, yields: null });
    expect(c?.calendar).toMatchObject({ releases: null, earnings: null });
  });
});

describe("the technicals report", () => {
  function writeReport(session: string, extra: Record<string, unknown> = {}): void {
    fs.mkdirSync(path.join(tmp, "technicals"), { recursive: true });
    fs.writeFileSync(
      path.join(tmp, "technicals", `report-${session}.json`),
      JSON.stringify({
        ok: true, report_version: 1, session, universe: 445, rules: { stage: "fit" },
        breadth: [{ session, leaders: 37, laggards: 203, net: -166, bullish_share: 0.154 }],
        stages: [
          { sector: "Technology", net: 3, leaders: [{ symbol: "MSFT", stage: "confirmed" }], laggards: [] },
          { sector: "Energy", net: -33, leaders: [], laggards: [{ symbol: "XOM", stage: "early" }] },
        ],
        rotation: { leading: ["IGV"], none: ["XLK"] },
        signals: { CciDipInBullishTrend: ["DELL"] },
        leaders: [{ symbol: "MRNA", sector: "Healthcare", rank: 10, return_6m_pct: 301.3, trend_short: "Bullish", trend_long: "Bullish", stage: "leader/confirmed" }],
        ...extra,
      }),
    );
  }

  it("pairs a morning with the last report BEFORE it — never the one its own close wrote", () => {
    writePack("2026-09-28", minimalPack("2026-09-28"));
    writeReport("2026-09-24");
    writeReport("2026-09-25");
    writeReport("2026-09-28");
    expect(readMorning(config).technicals?.session).toBe("2026-09-25");
  });

  it("passes the report through in its own order", () => {
    writePack("2026-09-28", minimalPack("2026-09-28"));
    writeReport("2026-09-25");
    const t = readMorning(config).technicals;
    expect(t?.stages.map((s) => s.sector)).toEqual(["Technology", "Energy"]);
    expect(t?.leaders[0]).toMatchObject({ symbol: "MRNA", return6mPct: 301.3, stage: "leader/confirmed" });
    expect(t?.breadth[0]).toMatchObject({ net: -166, bullishShare: 0.154 });
    expect(t?.rotation["leading"]).toEqual(["IGV"]);
  });

  it("no report, a failed one, or an unreadable one is null — the rest of the page is untouched", () => {
    writePack("2026-09-28", minimalPack("2026-09-28"));
    expect(readMorning(config).technicals).toBeNull();
    writeReport("2026-09-25", { ok: false });
    expect(readMorning(config).technicals).toBeNull();
    fs.writeFileSync(path.join(tmp, "technicals", "report-2026-09-25.json"), "{not json");
    const payload = readMorning(config);
    expect(payload.technicals).toBeNull();
    expect(payload.current?.session).toBe("2026-09-28");
  });
});
