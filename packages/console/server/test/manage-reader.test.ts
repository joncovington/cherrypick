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
    ruleset: "zhang-2026-10",
    rules: { ruleset: "zhang-2026-10" },
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
    };
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-19.json"), JSON.stringify(doc));
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-16.json"), JSON.stringify(evalDoc("2026-10-16")));

    const p = readManage(config, "2026-10-19").accounts[0]!.portfolio!;
    expect(p.bwd).toBe(5.85);
    expect(p.accountKind).toEqual({ marginOrCash: "Cash", typeName: "Traditional IRA" });
    expect(p.bands?.delta).toEqual({ low: -21.84, high: 32.76, basis: "theta", value: 5.85, inside: true });
    expect(p.bands?.theta.inside).toBe(false);
    expect(p.breakeven).toEqual({ kind: "short_gamma", points: 15.22, pct: 0.01994 });
    expect(p.guidance[0]!.drivers).toEqual([{ underlying: "GOOGL", value: 9.06 }]);
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
    // An evaluation from before the reading existed has no portfolio, not an empty one.
    expect(readManage(config, "2026-10-16").accounts[0]!.portfolio).toBeNull();
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
