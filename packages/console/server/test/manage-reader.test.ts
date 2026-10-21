import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readManage } from "../src/readers/manage.js";

/**
 * The position evaluation reader passes packages/manage's verdicts through and derives nothing. The
 * guards: the latest session is the default, the package's snake_case fields land where the page
 * reads them, an absent number stays null (never zero), and a missing store is an empty payload
 * rather than a thrown route.
 */

let tmp: string;
let config: ConsoleConfig;

function evalDoc(session: string): Record<string, unknown> {
  return {
    eval_version: 1,
    session,
    snapshot_generated_at: `${session}T10:00:02-0400`,
    ok: true,
    ruleset: "manage-2026-10",
    rules: { ruleset: "manage-2026-10" },
    rule_warnings: [],
    accounts: [
      {
        account: "****1234",
        net_liq: 20000,
        risk_budget: 400,
        pnl_gross: -120,
        counts: { CLOSE: 1, HOLD: 1 },
        trades: [
          {
            trade_id: "KO",
            underlying: "KO",
            kind: "debit",
            cost: 253,
            value: 106,
            pnl_gross: -147,
            capture: -0.58,
            nearest_short_dte: 38,
            recommendation: { action: "CLOSE", confidence: 65, rule: "debit.stop", reason: "down 58%", agreeing: [], conflicts: [] },
            fired: [{ rule: "debit.stop", action: "CLOSE", confidence: 65, reason: "down 58%", legs: null }],
            legs: [{ label: "+1 2026-11-20 87.5P", quantity: 1, mid: 1.22, open_price: 3.06, capture: -0.6, delta: -0.3, iv: 0.21 }],
            order: { legs: [{ side: "STC", quantity: 1, leg: "+1 2026-11-20 87.5P", mid: 1.22 }], net: 106, net_price: 1.06, net_side: "cr" },
            opening: { net: -253, net_price: 2.53, net_side: "db" },
            basis: "order_chain",
            fees: -2.25,
            pnl_net: -149.25,
            chain: {
              cash: -253,
              fees: -2.25,
              rolls: 0,
              orders: [{ date: "2026-10-07", role: "open", net: -253, net_price: 2.53, net_side: "db", fees: -2.25,
                legs: ["BTO 1 2026-11-20 87.5P @ 3.06", "STO 1 2026-11-20 80P @ 0.53"] }],
            },
            explanation: "You paid $253 for this, and it is worth $106 now, a loss of $147 (58%).",
            exit_levels: { stop: { close_value: 126.5, net_price: 1.265, net_side: "cr" }, time_exit: { date: "2026-10-30" } },
            earnings: { date: "2026-10-27", days: 14, time_of_day: "BTO", before_nearest_short_expiry: true },
            profile: null,
          },
          {
            trade_id: "IWM",
            underlying: "IWM",
            kind: "debit",
            pnl_gross: 27,
            recommendation: { action: "HOLD", confidence: 70, rule: "hold", reason: "no rule fired" },
            fired: [],
            legs: [],
          },
        ],
      },
    ],
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "manage-reader-"));
  fs.mkdirSync(path.join(tmp, "manage"));
  config = { paths: { manageDir: path.join(tmp, "manage") } } as unknown as ConsoleConfig;
});

describe("readManage", () => {
  it("is an empty payload when nothing has been evaluated", () => {
    config = { paths: { manageDir: path.join(tmp, "absent") } } as unknown as ConsoleConfig;
    const out = readManage(config);
    expect(out.session).toBeNull();
    expect(out.accounts).toEqual([]);
  });

  it("passes manage's pre-open flag through, and none on an evaluation without it", () => {
    const doc = { ...evalDoc("2026-10-21"), pre_open: { taken_at: "07:17", expiring_unpriced: ["SPCX"] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-21.json"), JSON.stringify(doc));
    expect(readManage(config).preOpen).toEqual({ takenAt: "07:17", expiringUnpriced: ["SPCX"] });
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-21.json"), JSON.stringify(evalDoc("2026-10-21")));
    expect(readManage(config).preOpen).toBeNull();
  });

  it("carries the watchdog's stale-snapshot WARN on the newest session only", () => {
    for (const s of ["2026-10-19", "2026-10-20"]) {
      fs.writeFileSync(path.join(tmp, "manage", `eval-${s}.json`), JSON.stringify(evalDoc(s)));
    }
    const wd = path.join(tmp, "watchdog.last.json");
    config = { paths: { manageDir: path.join(tmp, "manage"), watchdogLast: wd } } as unknown as ConsoleConfig;
    expect(readManage(config).snapshotStale).toBeNull(); // no watchdog run yet
    const message = "the 2026-10-20 snapshot is v11 and the installed code takes v14: views read empty";
    fs.writeFileSync(wd, JSON.stringify({ findings: [{ key: "manage.snapshot_version", status: "WARN", message }] }));
    expect(readManage(config).snapshotStale).toBe(message);
    expect(readManage(config, "2026-10-19").snapshotStale).toBeNull();
    fs.writeFileSync(wd, JSON.stringify({ findings: [{ key: "manage.snapshot_version", status: "OK", message: "current" }] }));
    expect(readManage(config).snapshotStale).toBeNull();
  });

  it("defaults to the latest session and passes the package's verdicts through", () => {
    for (const s of ["2026-10-13", "2026-10-14"]) {
      fs.writeFileSync(path.join(tmp, "manage", `eval-${s}.json`), JSON.stringify(evalDoc(s)));
    }
    const out = readManage(config);
    expect(out.session).toBe("2026-10-14");
    expect(out.sessions).toEqual(["2026-10-13", "2026-10-14"]);
    const [ko, iwm] = out.accounts[0]!.trades;
    expect(ko!.action).toBe("CLOSE");
    expect(ko!.confidence).toBe(65);
    expect(ko!.order?.netSide).toBe("cr");
    expect(ko!.opening).toEqual({ net: -253, netPrice: 2.53, netSide: "db" });
    expect(ko!.explanation).toContain("You paid $253");
    expect(ko!.basis).toBe("order_chain");
    expect(ko!.pnlNet).toBeCloseTo(-149.25);
    expect(ko!.chain?.orders[0]).toMatchObject({ role: "open", netPrice: 2.53, netSide: "db" });
    expect(ko!.chain?.orders[0]!.legs).toHaveLength(2);
    expect(iwm!.chain).toBeNull();
    expect(ko!.stop?.netPrice).toBeCloseTo(1.265);
    expect(ko!.timeExit).toBe("2026-10-30");
    expect(ko!.earnings?.beforeNearestShortExpiry).toBe(true);
    // Absent numbers stay null -- never a zero the page would print as $0.00.
    expect(iwm!.value).toBeNull();
    expect(iwm!.capture).toBeNull();
    expect(iwm!.order).toBeNull();
    expect(iwm!.opening).toBeNull();
    expect(iwm!.explanation).toBeNull();
    expect(out.accounts[0]!.counts).toEqual({ CLOSE: 1, HOLD: 1 });
    expect(out.accounts[0]!.excluded).toEqual([]);
  });

  it("passes the portfolio reading through, and an older evaluation has none", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["portfolio"] = {
      benchmark: "SPY",
      benchmark_spot: 763.28,
      complete: false,
      unmodelled_legs: [],
      no_beta: ["ABC"],
      account_type: { margin_or_cash: "Cash", type_name: "Traditional IRA" },
      bwd: 5.85,
      bwd_dollars_per_1pct: 44.63,
      theta: 43.68,
      theta_pct_net_liq: 0.00306,
      front_dte: 2,
      theta_ex_front: 40.1,
      vega_30: -8.87,
      breakeven: { kind: "short_gamma", points: 15.22, pct: 0.01994 },
      expected_move_pct: 0.01026,
      expected_move: 7.83,
      realised_move: null,
      buying_power_used: 7622.5,
      buying_power_used_pct: 0.534,
      stress: [{ shock: -0.1, pnl: -1569.16, pct_net_liq: -0.1099, left_out: ["ABC"] }],
      bands: {
        delta: { low: -21.84, high: 32.76, basis: "theta", value: 5.85, inside: true },
        theta: { low: 0.001, high: 0.003, value: 0.00306, inside: false },
        buying_power: { low: 0, high: 0.3, value: 0.534, vix: 19.99, inside: false },
      },
      vix: 19.99,
      net_liq: 14273.72,
      scenarios: {
        moves: [-0.1, 0, 0.1],
        iv_shift_weighting: "term",
        matrix: [{ iv_shift: 0.05, pnl: [-1437, -48, null] }],
        graph: { moves: [-0.15, 0, 0.15], curves: [{ horizon_days: 4, pnl: [-2533, 537, -4] }] },
        left_out: [],
      },
      ladder: {
        expiries: [{ expiration: "2026-11-20", trades: ["META"], downside: -6837, downside_unknown: 1, theta: 77.27 }],
        earnings: {
          trades: ["META"],
          theta: 35,
          theta_share: 0.724,
          downside: -4292.5,
          by_date: [{ date: "2026-10-28", trades: ["META"], downside: -4292.5, downside_unknown: 0 }],
        },
      },
      candidates: {
        source_session: "2026-10-19",
        taken_at: "2026-10-19T18:45:00+00:00",
        account_basis: "cash",
        names: 458,
        excluded: { "earnings inside the trade": 120 },
        fits: [
          {
            underlying: "AAL",
            kind: "short_put",
            alternative_to: null,
            expiry: "2026-11-20",
            legs: [{ strike: 11, right: "P", quantity: -1, bid: 0.31, ask: 0.35, delta: -0.4 }],
            units: 1,
            binding: "per-trade risk",
            per_unit: { credit: 0.33, natural: 0.31, cash: 33, max_loss: 1067, buying_power: 1067, stress_loss: 251.07, theta: 0.87, bwd: null },
            theta_per_bp: 0.00082,
            earnings_date: "2026-10-22",
            earnings_unknown: false,
          },
        ],
        fits_total: 1,
        too_big: [],
        too_big_total: 0,
        waiting: [
          {
            underlying: "XLF",
            kind: "call_spread",
            alternative_to: null,
            expiry: "2026-12-18",
            legs: [],
            units: 0,
            binding: "buying power",
            per_unit: { credit: 0.6, natural: 0.55, cash: 60, max_loss: 190, buying_power: 190, stress_loss: 190, theta: 0.5, bwd: -0.4 },
            theta_per_bp: 0.0026,
            if_room: {
              units: 2,
              binding: "per-trade risk",
              bp_after_pct: 0.4532,
              bp_cap_pct: 0.3,
              bp_level: "far_over",
              theta_after_pct: 0.0148,
              theta_ceiling_pct: 0.003,
              theta_over: true,
            },
          },
          { underlying: "ODD", kind: "put_spread", units: 0, binding: "buying power", if_room: { units: 1, bp_level: "sideways" } },
        ],
        waiting_total: 33,
        bp_used_pct: 0.4305,
        bp_cap_pct: 0.3,
        bp_warn_over: 0.1,
        error: null,
      },
      guidance: [
        { rule: "portfolio.theta", action: "REVIEW", confidence: 40, reason: "above the ceiling", drivers: [{ underlying: "GOOGL", theta: 9.06 }] },
      ],
      underlyings: [
        { underlying: "FXI", beta: 0.274, bwd: -0.61, theta: -1.34, vega_30: 3.93, bwd_share: 0.011, theta_share: 0.029,
          beta_doubt: "beta +0.27 but three-month correlation -0.75: opposite signs" },
      ],
      hedges: null,
      broker_check: {
        available: true,
        legs_compared: 30,
        legs_left_out: ["-1 GOOGL 370C"],
        bwd_ours: 5.85,
        bwd_broker: 7.1,
        bwd_gap: -1.25,
        theta_ours: 43.68,
        theta_broker: 41.2,
        theta_gap: 2.48,
        underlyings: [{ underlying: "META", legs: 4, bwd_ours: 6.2, bwd_broker: 7.0, bwd_gap: -0.8, theta_ours: 6.4, theta_broker: 6.0, theta_gap: 0.4 }],
        legs_on_broker_iv: 7,
        expiring: { legs: ["-2 SPCX 160C"], bwd_ours: 0, bwd_broker: -30.69, bwd_gap: 30.69, theta_ours: 180.5, theta_broker: 554.55, theta_gap: -374.05 },
      },
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-16.json"), JSON.stringify(evalDoc("2026-10-16")));

    const p = readManage(config, "2026-10-19").accounts[0]!.portfolio!;
    expect(p.bwd).toBe(5.85);
    expect([p.theta, p.thetaExFront, p.frontDte]).toEqual([43.68, 40.1, 2]);
    expect(p.accountKind).toEqual({ marginOrCash: "Cash", typeName: "Traditional IRA" });
    expect(p.bands?.delta).toEqual({ low: -21.84, high: 32.76, basis: "theta", value: 5.85, inside: true });
    expect(p.bands?.theta.inside).toBe(false);
    expect(p.breakeven).toEqual({ kind: "short_gamma", points: 15.22, pct: 0.01994 });
    expect(p.guidance[0]!.drivers).toEqual([{ underlying: "GOOGL", figures: [{ kind: "theta", value: 9.06 }] }]);
    expect(p.underlyings[0]!.betaDoubt).toMatch(/opposite signs/);
    expect(p.stress[0]!.leftOut).toEqual(["ABC"]);
    expect(p.complete).toBe(false);
    // Absent stays null, never zero.
    expect(p.realisedMove).toBeNull();
    expect(p.hedges).toBeNull();
    expect(p.netLiq).toBe(14273.72);
    expect(p.bands?.buyingPower).toEqual({ low: 0, high: 0.3, value: 0.534, vix: 19.99, inside: false });
    expect(p.scenarios?.ivShiftWeighting).toBe("term");
    expect(p.scenarios?.matrix[0]).toEqual({ ivShift: 0.05, pnl: [-1437, -48, null] }); // a missing cell stays null
    expect(p.scenarios?.graph.curves[0]).toEqual({ horizonDays: 4, pnl: [-2533, 537, -4] });
    expect(p.ladder?.expiries[0]).toEqual({ expiration: "2026-11-20", trades: ["META"], downside: -6837, downsideUnknown: 1, theta: 77.27 });
    expect(p.ladder?.earnings.thetaShare).toBe(0.724);
    expect(p.ladder?.earnings.byDate).toEqual([{ date: "2026-10-28", trades: ["META"], downside: -4292.5 }]);
    const c = p.candidates!;
    expect(c.accountBasis).toBe("cash");
    expect(c.excluded).toEqual({ "earnings inside the trade": 120 });
    expect(c.fits[0]).toMatchObject({ underlying: "AAL", kind: "short_put", units: 1, binding: "per-trade risk" });
    expect(c.fits[0]!.perUnit).toMatchObject({ credit: 0.33, buyingPower: 1067, stressLoss: 251.07, bwd: null });
    expect(c.fits[0]!.alternative).toBeNull();
    expect(c.fits[0]!.ifRoom).toBeNull();
    expect(c).toMatchObject({ waitingTotal: 33, bpUsedPct: 0.4305, bpCapPct: 0.3, bpWarnOver: 0.1 });
    expect(c.waiting[0]!.ifRoom).toEqual({
      units: 2,
      binding: "per-trade risk",
      bpAfterPct: 0.4532,
      bpCapPct: 0.3,
      bpLevel: "far_over",
      thetaAfterPct: 0.0148,
      thetaCeilingPct: 0.003,
      thetaOver: true,
    });
    // A grade the reader does not know is no grade, never a colour guessed at.
    expect(c.waiting[1]!.ifRoom!.bpLevel).toBeNull();
    expect(p.brokerCheck).toEqual({
      legsCompared: 30,
      legsLeftOut: ["-1 GOOGL 370C"],
      bwdOurs: 5.85,
      bwdBroker: 7.1,
      bwdGap: -1.25,
      thetaOurs: 43.68,
      thetaBroker: 41.2,
      thetaGap: 2.48,
      underlyings: [{ underlying: "META", legs: 4, bwdGap: -0.8, thetaGap: 0.4 }],
      legsOnBrokerIv: 7,
      expiring: { legs: ["-2 SPCX 160C"], bwdOurs: 0, bwdBroker: -30.69, bwdGap: 30.69, thetaOurs: 180.5, thetaBroker: 554.55, thetaGap: -374.05 },
    });
    // An evaluation from before the reading existed has no portfolio, not an empty one.
    expect(readManage(config, "2026-10-16").accounts[0]!.portfolio).toBeNull();
  });

  it("passes an earnings trade and the rules waiting for its call through, and an ordinary trade has neither", () => {
    const doc = evalDoc("2026-10-18");
    const trades = (doc["accounts"] as Array<Record<string, unknown>>)[0]!["trades"] as Array<Record<string, unknown>>;
    trades[1]!["earnings_trade"] = { key: "IWM|2026-10-23", report: "2026-10-20", time_of_day: "AMC", lead_days: 4, passed: false };
    trades[1]!["held_for_earnings"] = [{ rule: "credit.time_exit", action: "CLOSE", confidence: 75, reason: "losing", legs: null }];
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-18.json"), JSON.stringify(doc));
    const [ko, iwm] = readManage(config).accounts[0]!.trades;
    expect(iwm!.earningsTrade).toEqual({ report: "2026-10-20", timeOfDay: "AMC", leadDays: 4, passed: false });
    expect(iwm!.heldForEarnings.map((f) => f.rule)).toEqual(["credit.time_exit"]);
    expect(ko!.earningsTrade).toBeNull();
    expect(ko!.heldForEarnings).toEqual([]);
  });

  it("carries each guidance driver's figures with their kind, and both shares for concentration", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["portfolio"] = {
      benchmark: "SPY",
      guidance: [
        { rule: "portfolio.delta", confidence: 40, reason: "outside", drivers: [{ underlying: "META", bwd: 6.22 }] },
        // The cap is breached by the delta share; the theta share comes first in the row and is smaller.
        {
          rule: "portfolio.concentration",
          confidence: 40,
          reason: "over 25%",
          drivers: [{ underlying: "SPY", theta_share: 0.128, bwd_share: 0.31 }],
        },
      ],
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const [delta, conc] = readManage(config).accounts[0]!.portfolio!.guidance;
    expect(delta!.drivers).toEqual([{ underlying: "META", figures: [{ kind: "bwd", value: 6.22 }] }]);
    expect(conc!.drivers).toEqual([
      { underlying: "SPY", figures: [{ kind: "bwd_share", value: 0.31 }, { kind: "theta_share", value: 0.128 }] },
    ]);
  });

  it("reads the caption figures from the evaluation's own ruleset, and none where it embeds none", () => {
    const doc = evalDoc("2026-10-19");
    doc["rules"] = {
      ruleset: "manage-2026-10",
      portfolio: {
        theta: { confidence: 55 },
        delta: { confidence: 50 },
        breakeven: { confidence: 55 },
        buying_power: { confidence: 60 },
        concentration: { confidence: 55 },
        candidates: { per_trade_risk_pct: 0.02, notional_per_name_pct: { cash: 0.25, margin: 0.5 }, max_name_theta_share: 0.2 },
      },
    };
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["portfolio"] = { benchmark: "SPY", candidates: { account_basis: "margin", fits: [], too_big: [] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const p = readManage(config).accounts[0]!.portfolio!;
    expect(p.ruleConfidence).toEqual({ low: 50, high: 60 });
    // A margin account is held to the margin notional cap, not the cash one.
    expect(p.candidates!.caps).toEqual({ perTradeRiskPct: 0.02, notionalPerNamePct: 0.5, maxNameThetaShare: 0.2 });

    (acct["portfolio"] as Record<string, unknown>)["candidates"] = { account_basis: "cash", fits: [], too_big: [] };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    expect(readManage(config).accounts[0]!.portfolio!.candidates!.caps!.notionalPerNamePct).toBe(0.25);

    doc["rules"] = { ruleset: "manage-2026-10" };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const bare = readManage(config).accounts[0]!.portfolio!;
    expect(bare.ruleConfidence).toBeNull();
    expect(bare.candidates!.caps).toBeNull();
  });

  it("passes the same-expiry badge through, false on an older evaluation", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    const cand = (u: string, held?: boolean) => ({
      underlying: u,
      kind: "put_spread",
      legs: [],
      units: 1,
      per_unit: {},
      ...(held === undefined ? {} : { held_same_expiry: held }),
    });
    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [cand("NKE", true), cand("SLV", false), cand("OLD")], too_big: [] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const fits = readManage(config).accounts[0]!.portfolio!.candidates!.fits;
    expect(fits.map((c) => [c.underlying, c.heldSameExpiry])).toEqual([
      ["NKE", true],
      ["SLV", false],
      ["OLD", false],
    ]);
  });

  it("passes a spread's odds through, none on an older evaluation", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    const odds = { p_loss: 0.2, p_max_loss: 0.11, break_even: 23.66, entry_cost: 4.74, entry_cost_share: 0.13 };
    const cand = { underlying: "CCL", kind: "put_spread", legs: [], units: 1, per_unit: {} };
    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [{ ...cand, odds }, { ...cand, underlying: "OLD" }], too_big: [] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const fits = readManage(config).accounts[0]!.portfolio!.candidates!.fits;
    expect(fits[0]!.odds).toEqual({ pLoss: 0.2, pMaxLoss: 0.11, breakEven: 23.66, entryCost: 4.74, entryCostShare: 0.13 });
    expect(fits[1]!.odds).toBeNull();
  });

  it("passes the ex-earnings list through as its own list, and a refused one with no list", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    const cand = (u: string, units: number) => ({ underlying: u, kind: "put_spread", legs: [], units, per_unit: {} });
    const ex = { trades: ["NFLX", "UAL"], unpriced: [], dollars: 595, error: null, fits: [cand("SLV", 2)], too_big: [], waiting: [], bp_used_pct: 0.39 };
    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [], too_big: [], waiting: [cand("SLV", 0)], bp_used_pct: 0.43, ex_earnings: ex } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const c = readManage(config).accounts[0]!.portfolio!.candidates!;
    expect(c.bpUsedPct).toBe(0.43);
    expect(c.exEarnings).toMatchObject({ trades: ["NFLX", "UAL"], dollars: 595, error: null });
    expect(c.exEarnings!.list!.bpUsedPct).toBe(0.39);
    expect(c.exEarnings!.list!.fits.map((r) => [r.underlying, r.units])).toEqual([["SLV", 2]]);
    expect(c.exEarnings!.list!.exEarnings).toBeNull();

    const refused = { trades: ["TSLA"], unpriced: ["TSLA"], dollars: null, error: "cannot free it" };
    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [], too_big: [], ex_earnings: refused } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const r = readManage(config).accounts[0]!.portfolio!.candidates!.exEarnings!;
    expect(r).toMatchObject({ unpriced: ["TSLA"], dollars: null, error: "cannot free it", list: null });

    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [], too_big: [] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    expect(readManage(config).accounts[0]!.portfolio!.candidates!.exEarnings).toBeNull();
  });

  it("badges a candidate from the session's own income lists, and none without them", () => {
    const doc = evalDoc("2026-10-19");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    const cand = (u: string) => ({ underlying: u, kind: "put_spread", legs: [], units: 0, per_unit: {} });
    acct["portfolio"] = { benchmark: "SPY", candidates: { fits: [], too_big: [], waiting: [cand("TSLL"), cand("SLV")] } };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    const tech = path.join(tmp, "technicals");
    config = { paths: { manageDir: path.join(tmp, "manage"), technicalsDir: tech } } as unknown as ConsoleConfig;
    const none = readManage(config).accounts[0]!.portfolio!.candidates!.waiting;
    expect(none.map((c) => c.leveraged)).toEqual([null, null]);
    fs.mkdirSync(path.join(tech, "income-lists"), { recursive: true });
    const lists = { "short-puts": [{ symbol: "TSLL", leveraged: "leveraged" }, { symbol: "SLV", leveraged: null }], "covered-calls": [] };
    fs.writeFileSync(path.join(tech, "income-lists", "2026-10-19.json"), JSON.stringify({ lists }));
    const badged = readManage(config).accounts[0]!.portfolio!.candidates!.waiting;
    expect(badged.map((c) => [c.underlying, c.leveraged])).toEqual([
      ["TSLL", "leveraged"],
      ["SLV", null],
    ]);
  });

  it("an evaluation written before the wheel view has no wheel, never an empty one", () => {
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-14.json"), JSON.stringify(evalDoc("2026-10-14")));
    const a = readManage(config).accounts[0]!;
    expect(a.wheel).toBeNull();
    expect(a.trades[0]!.wheel).toBeNull();
  });

  it("passes the wheel cycles and the wheel rules' notes through, keeping every null null", () => {
    // A cycle as packages/manage's evaluator writes it (test_wheel_rules: a put sold alone, tested).
    const doc = evalDoc("2026-10-20");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["wheel"] = {
      cycles: [
        {
          underlying: "XYZ", status: "open", reconciled: true, problems: [], flat: false, start: "2026-10-01",
          start_known: true, end: null, days: 12, assignments: 0, called_away: 0, shares: 0.0, basis_known: true,
          lots: [], open_options: [{ symbol: "XYZ 261120P00050000", leg: "2026-11-20 50P", quantity: -1.0 }],
          credits: 100.0, debits: 0, net_cash: 100.0, fees: -1.1, net_premium: 100.0, dividends: 0.0,
          tax_basis: null, wheel_basis: null, breakeven: { price: 49.011, basis: "put strike less net cash" },
          option_pnl: { realised: 0.0, unrealised: -20.0 }, stock_pnl: { realised: 0.0, unrealised: 0.0 },
          pnl_net: -21.1,
          skate_yield: { value: -0.128, capped: false, capital: 5000.0, basis: "comparison figure: net P&L / time-weighted capital x 365 / days" },
          events: [{ date: "2026-10-01", what: "open", legs: ["STO 1 2026-11-20 50P @ 1.00"], cash: 100.0, fees: -1.1 }],
          today: [
            {
              leg: "2026-11-20 50P", symbol: "XYZ 261120P00050000", right: "P", strike: 50.0, expiration: "2026-11-20",
              dte: 38, quantity: -1.0, spot: 50.0, mid: 1.2, open_price: 1.0, wide: null, tested: true,
              intrinsic: 0.0, extrinsic: 1.2, extrinsic_dollars: 120.0, band: null,
              band_missing: "no band for the name in the snapshot", beyond_band: null,
              resting_btc: [{ order_id: 9, status: "Live", price: 0.1, quantity: 1, time_in_force: "GTC", legs: 1 }],
              btc_price: 0.1, mark_at_or_under_btc: false,
            },
          ],
          trade_ids: ["KO"],
        },
        {
          underlying: "ABC", status: "unreconciled", reconciled: false,
          problems: ["the history starts after the cycle did"], start: "2026-09-01", start_known: false, end: null,
          days: null, shares: 100, basis_known: false, lots: [{ date: null, shares: 100, price: null, source: "before the history" }],
          open_options: [], credits: null, debits: null, net_cash: null, fees: 0, net_premium: null, dividends: null,
          tax_basis: null, wheel_basis: null, breakeven: null,
          option_pnl: { realised: null, unrealised: null }, stock_pnl: { realised: null, unrealised: null },
          pnl_net: null, skate_yield: { value: null, capped: false, capital: null, basis: "comparison figure" },
          events: [], today: [], trade_ids: [],
        },
      ],
      flagged: [{ underlying: "ABC", date: "2026-09-03", kind: "share delivery", row: "Receive Deliver 100 ABC" }],
      outside: { ABC: ["2026-08-01: STO 1 ABC (no cycle running)"] },
      bands_day: null,
      bands_missing: "the snapshot carries no skate bands",
      error: null,
    };
    const ko = (acct["trades"] as Array<Record<string, unknown>>)[0]!;
    ko["wheel"] = [
      {
        underlying: "XYZ", start: "2026-10-01", status: "open",
        take_profit: [{ leg: "2026-11-20 50P", mid: 1.2, btc_price: 0.03, source: "the default", hit: false }],
        rolls: [
          {
            leg: "2026-11-20 50P", expiration: "2026-12-18", quoted_at: "2026-10-12T18:45:00Z", why: null,
            net_zero: {
              close_price: 1.2, open: { strike: 47.0, bid: 1.7, ask: 1.9, mid: 1.8 }, net: 0.6, expiration: "2026-12-18",
              legs: ["BTC 1 2026-11-20 50P @ 1.20", "STO 1 2026-12-18 47P @ 1.80 (quoted 1.70/1.90)"],
              check: { verdict: "ok", credit: 60, max_loss_before: 4850, max_loss_after: null, problems: [], notes: ["n"], after: ["-1 2026-12-18 47P"] },
              check_error: null,
            },
            premium_gambit: null,
            premium_gambit_why: "the price the short was sold at is unknown",
          },
        ],
        next_leg: null,
      },
    ];
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-20.json"), JSON.stringify(doc));
    const a = readManage(config).accounts[0]!;
    const w = a.wheel!;
    expect(w.error).toBeNull();
    expect(w.bandsMissing).toBe("the snapshot carries no skate bands");
    expect(w.flagged).toEqual([{ underlying: "ABC", date: "2026-09-03", kind: "share delivery", row: "Receive Deliver 100 ABC" }]);
    expect(w.outside).toEqual({ ABC: ["2026-08-01: STO 1 ABC (no cycle running)"] });
    const [xyz, abc] = w.cycles;
    expect(xyz).toMatchObject({
      underlying: "XYZ", status: "open", netCash: 100, fees: -1.1, netPremium: 100, pnlNet: -21.1, days: 12,
      breakeven: { price: 49.011, basis: "put strike less net cash" }, tradeIds: ["KO"], basisKnown: true,
    });
    expect(xyz!.optionPnl).toEqual({ realised: 0, unrealised: -20 });
    expect(xyz!.today[0]).toMatchObject({ tested: true, extrinsicDollars: 120, btcPrice: 0.1, markAtOrUnderBtc: false });
    expect(xyz!.today[0]!.restingBtc[0]!.orderId).toBe("9");
    // Nulls stay null: no basis with no shares, no band, an unset `wide` is not false.
    expect(xyz!.taxBasis).toBeNull();
    expect(xyz!.wheelBasis).toBeNull();
    expect(xyz!.today[0]!.band).toBeNull();
    expect(xyz!.today[0]!.beyondBand).toBeNull();
    expect(xyz!.today[0]!.wide).toBeNull();
    expect(abc).toMatchObject({ status: "unreconciled", reconciled: false, basisKnown: false, startKnown: false });
    expect(abc!.problems).toEqual(["the history starts after the cycle did"]);
    expect(abc!.credits).toBeNull();
    expect(abc!.days).toBeNull();
    expect(abc!.breakeven).toBeNull();
    expect(abc!.stockPnl).toEqual({ realised: null, unrealised: null });
    expect(abc!.pnlNet).toBeNull();
    expect(abc!.skateYield!.value).toBeNull();
    expect(abc!.lots[0]).toEqual({ date: null, shares: 100, price: null, source: "before the history" });
    // The trade's wheel notes: the rolls with their check-roll verdicts.
    const note = a.trades[0]!.wheel![0]!;
    expect(note.takeProfit![0]).toEqual({ leg: "2026-11-20 50P", mid: 1.2, btcPrice: 0.03, source: "the default", hit: false });
    const roll = note.rolls![0]!;
    expect(roll.netZero!.check).toMatchObject({ verdict: "ok", credit: 60, maxLossBefore: 4850, maxLossAfter: null });
    expect(roll.netZero!.open).toEqual({ strike: 47, bid: 1.7, ask: 1.9, mid: 1.8 });
    expect(roll.premiumGambit).toBeNull();
    expect(roll.premiumGambitWhy).toBe("the price the short was sold at is unknown");
    expect(roll.netZeroWhy).toBeNull();
    expect(note.nextLeg).toBeNull();
    expect(a.trades[1]!.wheel).toBeNull();
  });

  it("an evaluation written before the PMCC view has no PMCC, never an empty one", () => {
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-14.json"), JSON.stringify(evalDoc("2026-10-14")));
    const a = readManage(config).accounts[0]!;
    expect(a.pmcc).toBeNull();
    expect(a.trades[0]!.pmcc).toBeNull();
  });

  it("passes the PMCC view and the trade's PMCC note through, keeping every null null", () => {
    // As packages/manage's evaluator writes it (test_pmcc: a failed width with one quoted roll).
    const doc = evalDoc("2026-10-20");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["pmcc"] = {
      positions: [
        {
          underlying: "XYZ", recognized: true, recognized_by: "entered at 0.82 delta (at least 0.80)", intent: null,
          reconciled: true, problems: [], spot: 43.0, div_yield: 0.0,
          long: {
            symbol: "XYZ 280121C00040000", leg: "2028-01-21 40C", strike: 40.0, expiration: "2028-01-21", dte: 465,
            quantity: 1.0, mid: 8.0, opened: "2026-04-01", days_at_entry: 660, delta_at_entry: 0.8248,
            delta_at_entry_why: null,
            entry_fills: [{ date: "2026-04-01", price: 15.12, close: 50.0, div_yield: 0.0, iv: 0.30, delta: 0.8248 }],
            delta: 0.7075, intrinsic: 3.0, extrinsic: 5.0, extrinsic_share: 0.625, roll: [], roll_delta_unknown: false,
          },
          short: {
            symbol: "XYZ 261120C00045000", leg: "2026-11-20 45C", strike: 45.0, expiration: "2026-11-20", dte: 38,
            quantity: -1.0, mid: 0.8, open_price: 6.0, tested: false, intrinsic: 0.0, extrinsic: 0.8, prob_itm: 0.3038,
            band: null, band_missing: "band not in the snapshot", beyond_band: null,
            assignment: { near_zero: false, dividend: false, exposed: false, why: null },
            earnings: { date: null, inside: null, why: "no report date in the snapshot" },
          },
          width: {
            width: 5.0, net_cost: 762.0, net_cost_per_share: 7.62, basis: "linked orders", passes: false,
            pnl_if_assigned: -262.0, pnl_if_assigned_net: -265.3, assumes: "the long exercised to deliver the shares",
          },
          shorts: [
            { symbol: "XYZ 260515C00055000", leg: "2026-05-15 55C", sold: "2026-04-01", sold_price: 1.5, cash: 150.0, fees: -1.1, status: "expiration" },
            { symbol: "XYZ 261120C00045000", leg: "2026-11-20 45C", sold: "2026-09-25", sold_price: 6.0, cash: 600.0, fees: -1.1, status: "open" },
          ],
          orders: [1, 2],
          net_cash: -762.0, fees: -3.3, long_cost: 1512.0, premiums: 750.0,
          skate_yield: { value: 0.829, capped: false, basis: "comparison figure" },
          rolls: {
            candidates: [
              {
                expiration: "2026-12-18", open: { strike: 48.0, bid: 0.75, ask: 0.85, mid: 0.8 }, delta: 0.246,
                close_price: 0.8, net: 0.0,
                legs: ["BTC 1 2026-11-20 45C @ 0.80", "STO 1 2026-12-18 48C @ 0.80 (quoted 0.75/0.85)"],
                check: { verdict: "ok", credit: 0.0, max_loss_before: 762.0, max_loss_after: 762.0, problems: [], notes: [], after: [] },
                check_error: null,
              },
            ],
            why: null, quoted_expiries: ["2026-12-18"], quoted_at: "2026-10-12T18:45:00Z",
          },
          trade_ids: ["KO"],
        },
      ],
      underlying_history_error: null,
      bands_missing: "band not in the snapshot",
      error: null,
    };
    (acct["trades"] as Array<Record<string, unknown>>)[0]!["pmcc"] = {
      underlying: "XYZ", long: "2028-01-21 40C", short: "2026-11-20 45C", recognized: true,
      recognized_by: "entered at 0.82 delta (at least 0.80)", reconciled: true, width_passes: false,
      pnl_if_assigned: -262.0, assignment_exposed: false, roll_long: [],
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-20.json"), JSON.stringify(doc));
    const a = readManage(config).accounts[0]!;
    const p = a.pmcc!.positions[0]!;
    expect(a.pmcc!.error).toBeNull();
    expect(a.pmcc!.bandsMissing).toBe("band not in the snapshot");
    expect(p).toMatchObject({ underlying: "XYZ", recognized: true, reconciled: true, longCost: 1512, premiums: 750, orders: ["1", "2"], tradeIds: ["KO"] });
    expect(p.width).toMatchObject({ passes: false, pnlIfAssigned: -262, pnlIfAssignedNet: -265.3, netCostPerShare: 7.62, basis: "linked orders" });
    expect(p.long).toMatchObject({ deltaAtEntry: 0.8248, deltaAtEntryWhy: null, extrinsicShare: 0.625, roll: [], rollDeltaUnknown: false });
    expect(p.long.entryFills[0]).toMatchObject({ divYield: 0, iv: 0.3 });
    expect(p.short.assignment).toEqual({ nearZero: false, dividend: false, exposed: false, why: null, exDividendDate: null, dividendAmount: null });
    expect(p.short.earnings).toEqual({ date: null, inside: null, why: "no report date in the snapshot" });
    expect(p.short.band).toBeNull();
    expect(p.short.probItm).toBeCloseTo(0.3038);
    expect(p.shorts.map((s) => s.status)).toEqual(["expiration", "open"]);
    expect(p.rolls!.candidates[0]).toMatchObject({ delta: 0.246, net: 0, check: { verdict: "ok", maxLossAfter: 762 } });
    expect(p.rolls!.quotedAt).toBe("2026-10-12T18:45:00Z");
    expect(a.trades[0]!.pmcc).toMatchObject({ widthPasses: false, assignmentExposed: false, pnlIfAssigned: -262, error: null });
    expect(a.trades[1]!.pmcc).toBeNull();
  });

  it("an unknown delta at entry, an unpriced roll and an unjudged exposure stay null", () => {
    const doc = evalDoc("2026-10-20");
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    acct["pmcc"] = {
      positions: [
        {
          underlying: "ABC", recognized: false, recognized_by: "delta at entry unknown (no close for 2026-04-01 in the snapshot)",
          reconciled: false, problems: ["the long's opening predates the history"],
          long: { symbol: "L", leg: "L", delta_at_entry: null, delta_at_entry_why: "no close for 2026-04-01 in the snapshot", delta: null, roll: [] },
          short: { symbol: "S", leg: "S", tested: null, assignment: { near_zero: null, dividend: null, exposed: null, why: null }, earnings: {} },
          width: { passes: null, pnl_if_assigned: null, pnl_if_assigned_net: null, basis: "open_legs" },
          shorts: [], orders: [], net_cash: null, fees: 0, long_cost: null, premiums: null,
          skate_yield: { value: null, capped: false, basis: "comparison figure" }, rolls: null, trade_ids: [],
        },
      ],
      underlying_history_error: "the snapshot carries no underlying history (taken before v13)",
      bands_missing: null,
      error: null,
    };
    (acct["trades"] as Array<Record<string, unknown>>)[0]!["pmcc"] = { error: "PMCC rules failed: KeyError: 'x'" };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-20.json"), JSON.stringify(doc));
    const a = readManage(config).accounts[0]!;
    const p = a.pmcc!.positions[0]!;
    expect(p.recognized).toBe(false);
    expect(p.long.deltaAtEntry).toBeNull();
    expect(p.long.deltaAtEntryWhy).toBe("no close for 2026-04-01 in the snapshot");
    expect(p.long.rollDeltaUnknown).toBeNull();
    expect(p.short.tested).toBeNull();
    expect(p.short.assignment.exposed).toBeNull();
    expect(p.short.earnings).toEqual({ date: null, inside: null, why: null });
    expect(p.width.passes).toBeNull();
    expect(p.width.pnlIfAssigned).toBeNull();
    expect(p.rolls).toBeNull();
    expect(p.longCost).toBeNull();
    expect(a.pmcc!.underlyingHistoryError).toBe("the snapshot carries no underlying history (taken before v13)");
    expect(a.trades[0]!.pmcc).toMatchObject({ error: "PMCC rules failed: KeyError: 'x'", widthPasses: null, recognized: null });
  });

  it("an account with no wheel view says why", () => {
    const doc = evalDoc("2026-10-20");
    (doc["accounts"] as Array<Record<string, unknown>>)[0]!["wheel"] = { cycles: [], error: "no trade history in this snapshot" };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-20.json"), JSON.stringify(doc));
    const w = readManage(config).accounts[0]!.wheel!;
    expect(w.cycles).toEqual([]);
    expect(w.error).toBe("no trade history in this snapshot");
    expect(w.bandsMissing).toBeNull();
  });

  it("reads an older session when asked", () => {
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-13.json"), JSON.stringify(evalDoc("2026-10-13")));
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-14.json"), "{not json");
    expect(readManage(config, "2026-10-13").session).toBe("2026-10-13");
    const bad = readManage(config, "2026-10-14");
    expect(bad.error).toMatch(/unreadable/);
    expect(bad.accounts).toEqual([]);
  });

  it("passes where each figure came from: a leg's IV source, a roll's quotes and the PMCC limits (2026-10-21)", () => {
    const doc = evalDoc("2026-10-21");
    doc["rules"] = { ruleset: "manage-2026-10", pmcc: { recognize: { min_long_days: 180, max_short_days: 60, entry_min_delta: 0.8 } } };
    const acct = (doc["accounts"] as Array<Record<string, unknown>>)[0]!;
    const trades = acct["trades"] as Array<Record<string, unknown>>;
    trades[0]!["legs"] = [
      { label: "-1 2026-11-20 42C", quantity: -1, mid: 0.92, iv: 0.19, iv_source: "broker", wide: true },
      { label: "+1 2029-01-19 80C", quantity: 1, mid: 7.1, iv: null, iv_source: "unpriceable", wide: false },
      { label: "+1 2026-11-20 87.5P", quantity: 1, mid: 1.22, iv: 0.21 },
    ];
    trades[0]!["wheel"] = [
      {
        underlying: "KO", start: "2026-10-01", status: "open", problems: null, take_profit: [],
        rolls: [
          {
            leg: "2026-11-20 50P", expiration: "2026-12-04", quoted_at: "2026-10-21T14:35:47-0600", source: "roll-out chains",
            considered: [{ expiration: "2026-11-27", net_zero: false }, { expiration: "2026-12-04", net_zero: true }],
            why: null, net_zero: null, premium_gambit: null,
          },
          { leg: "2026-11-20 55P", expiration: "2026-12-18", quoted_at: "2026-10-21T18:45:01+00:00", why: null },
        ],
        next_leg: { strike: 60, band: 59.4, band_kind: "fixed", expiration: "2026-12-18", quote: { strike: 60, bid: 1, ask: 1.2, mid: 1.1 }, quoted_at: "2026-10-21T18:45:01+00:00", contracts: 1 },
      },
    ];
    acct["pmcc"] = {
      positions: [
        {
          underlying: "XYZ", long: {}, short: {}, width: {},
          rolls: { candidates: [], why: null, quoted_expiries: [], quoted_at: "2026-10-21T18:45:01+00:00", chains_taken_at: "2026-10-21T14:35:47-0600" },
        },
      ],
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-21.json"), JSON.stringify(doc));
    const a = readManage(config).accounts[0]!;
    const [broker, unpriceable, older] = a.trades[0]!.legs;
    expect(broker).toMatchObject({ iv: 0.19, ivSource: "broker", wide: true });
    expect(unpriceable).toMatchObject({ iv: null, ivSource: "unpriceable", wide: false });
    expect(older).toMatchObject({ iv: 0.21, ivSource: null, wide: null }); // before #504: unknown, never "mid"
    const [chains, income] = a.trades[0]!.wheel![0]!.rolls!;
    expect(chains).toMatchObject({ source: "roll-out chains", quotedAt: "2026-10-21T14:35:47-0600" });
    expect(chains!.considered).toEqual([
      { expiration: "2026-11-27", netZero: false },
      { expiration: "2026-12-04", netZero: true },
    ]);
    expect(income).toMatchObject({ source: null, considered: [] });
    expect(a.trades[0]!.wheel![0]!.nextLeg!.quotedAt).toBe("2026-10-21T18:45:01+00:00");
    const p = a.pmcc!;
    expect(p.positions[0]!.rolls).toMatchObject({ quotedAt: "2026-10-21T18:45:01+00:00", chainsTakenAt: "2026-10-21T14:35:47-0600" });
    expect(p.recognize).toEqual({ entryMinDelta: 0.8, minLongDays: 180, maxShortDays: 60 });
    // An evaluation embedding no PMCC rules names no limits.
    const bare = evalDoc("2026-10-21");
    (bare["accounts"] as Array<Record<string, unknown>>)[0]!["pmcc"] = { positions: [] };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-21.json"), JSON.stringify(bare));
    expect(readManage(config).accounts[0]!.pmcc!.recognize).toBeNull();
  });
});
