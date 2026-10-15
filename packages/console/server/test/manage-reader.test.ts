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

  it("reads an older session when asked", () => {
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-13.json"), JSON.stringify(evalDoc("2026-10-13")));
    fs.writeFileSync(path.join(tmp, "manage", "eval-2026-10-14.json"), "{not json");
    expect(readManage(config, "2026-10-13").session).toBe("2026-10-13");
    const bad = readManage(config, "2026-10-14");
    expect(bad.error).toMatch(/unreadable/);
    expect(bad.accounts).toEqual([]);
  });
});
