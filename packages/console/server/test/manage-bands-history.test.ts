import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readManageBandsHistory } from "../src/readers/manage.js";

/**
 * The bands over time (docs/console-review-plan.md batch 5): every stored evaluation's bands, each
 * session's own bounds as that day's evaluation stated them. The guards: oldest first, sessions
 * without bands left out, an unreadable file skipped, the theta figure the band judged (without the
 * front legs) preferred, and a missing band null rather than zero.
 */

let tmp: string;
let config: ConsoleConfig;

function write(session: string, doc: unknown): void {
  fs.writeFileSync(path.join(tmp, `eval-${session}.json`), typeof doc === "string" ? doc : JSON.stringify(doc));
}

function withBands(netLiq: number, delta: number, inside: boolean, buyingPower = true) {
  return {
    accounts: [
      {
        account: "****0000",
        portfolio: {
          net_liq: netLiq,
          theta: -40,
          theta_ex_front: 20,
          bands: {
            delta: { low: -10, high: 10, value: delta, inside, basis: "theta" },
            theta: { low: 0.001, high: 0.003, value: 0.002, inside: true },
            ...(buyingPower ? { buying_power: { low: 0, high: 0.35, value: 0.4, inside: false, vix: 21 } } : {}),
          },
        },
      },
    ],
  };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "manage-bands-"));
  config = { paths: { manageDir: tmp } } as unknown as ConsoleConfig;
});

describe("readManageBandsHistory", () => {
  it("lists each session's own bands oldest first, skipping sessions without bands and unreadable files", () => {
    write("2026-10-14", { accounts: [{ account: "****0000", portfolio: null }] });
    write("2026-10-20", withBands(14000, 12, false));
    write("2026-10-19", withBands(15000, 3, true, false));
    write("2026-10-21", "{ not json");
    const out = readManageBandsHistory(config);
    expect(out.accounts).toHaveLength(1);
    const [a] = out.accounts;
    expect(a.account).toBe("****0000");
    expect(a.sessions.map((s) => s.session)).toEqual(["2026-10-19", "2026-10-20"]);
    expect(a.sessions[1].delta).toEqual({ low: -10, high: 10, value: 12, inside: false });
    expect(a.sessions[1].netLiq).toBe(14000);
    expect(a.sessions[1].theta).toBe(20);
    expect(a.sessions[0].buyingPower).toBeNull();
    expect(a.sessions[1].buyingPower?.inside).toBe(false);
  });

  it("is empty, not a thrown route, when the store is missing", () => {
    config = { paths: { manageDir: path.join(tmp, "absent") } } as unknown as ConsoleConfig;
    expect(readManageBandsHistory(config)).toEqual({ accounts: [] });
  });
});
