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
        earnings: { trades: ["META"], theta: 35, theta_share: 0.724, downside: -4292.5 },
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
      },
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-16.json"), JSON.stringify(evalDoc("2026-10-16")));

    const p = readManage(config, "2026-10-19").accounts[0]!.portfolio!;
    expect(p.bwd).toBe(5.85);
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
    const c = p.candidates!;
    expect(c.accountBasis).toBe("cash");
    expect(c.excluded).toEqual({ "earnings inside the trade": 120 });
    expect(c.fits[0]).toMatchObject({ underlying: "AAL", kind: "short_put", units: 1, binding: "per-trade risk" });
    expect(c.fits[0]!.perUnit).toMatchObject({ credit: 0.33, buyingPower: 1067, stressLoss: 251.07, bwd: null });
    expect(c.fits[0]!.alternative).toBeNull();
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
});
