import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageAccount, ManageLeg } from "@console/shared";
import { PricingMethod, PricingTable, pricingBox, pricingByName } from "../src/pages/Live/PricingBox";

/**
 * Live → positions, pricing (2026-11-07): how the evaluation prices a position, and the dividend
 * yield and IV source each name's legs were modelled at.
 */

function leg(over: Partial<ManageLeg> = {}): ManageLeg {
  return {
    label: "+1 2028-12-15 95C",
    quantity: 1,
    expiration: "2028-12-15",
    dte: 770,
    openPrice: 11,
    mid: 11,
    capture: 0,
    delta: 0.5,
    iv: 0.16,
    ivSource: "mid",
    divYield: 0.045,
    divSource: "history",
    wide: false,
    ...over,
  };
}

function account(trades: Array<{ underlying: string; legs: ManageLeg[] }>): ManageAccount {
  return { trades } as unknown as ManageAccount;
}

const text = (node: React.ReactNode) => renderToString(<>{node}</>).replace(/<!-- -->/g, "");

describe("each name's pricing", () => {
  const accounts = [
    account([
      { underlying: "XYZ", legs: [leg(), leg({ quantity: -1, ivSource: "broker", wide: true })] },
      { underlying: "ABC", legs: [leg({ divYield: 0, divSource: "none", ivSource: "unpriceable", iv: null })] },
      { underlying: "XYZ", legs: [leg({ label: "100 shares", expiration: null, divYield: null, divSource: null })] },
    ]),
  ];

  it("counts option legs per name, in name order, and leaves shares out", () => {
    const rows = pricingByName(accounts);
    expect(rows.map((r) => r.name)).toEqual(["ABC", "XYZ"]);
    expect(rows[1]).toMatchObject({ legs: 2, broker: 1, unpriceable: 0, divYield: 0.045, divSource: "history" });
    expect(rows[0]).toMatchObject({ legs: 1, unpriceable: 1, divSource: "none" });
  });

  it("shows the yield, says when a name has no record, and when an evaluation predates the yield", () => {
    const rows = pricingByName(accounts);
    const out = text(<PricingTable rows={rows} />);
    expect(out).toContain("4.50%");
    expect(out).toContain("0% (no dividend record)");
    const old = pricingByName([account([{ underlying: "XYZ", legs: [leg({ divYield: null, divSource: null })] }])]);
    expect(text(<PricingTable rows={old} />)).toContain("not carried");
    expect(text(<PricingTable rows={[]} />)).toContain("No option legs");
  });
});

describe("the method", () => {
  it("says what is the broker's, what is modelled, the rate, and why the dividend matters", () => {
    const out = text(<PricingMethod rate={0.04} />);
    expect(out).toContain("What is the broker&#x27;s");
    expect(out).toContain("4.00%");
    expect(out).toContain("Why the dividend matters");
    expect(out).toContain("no interest rate or dividend"); // the broker's IV is on its own terms
    expect(out).toContain("Unpriceable legs");
  });

  it("is a two-tab box the positions page opens as ?box=pricing", () => {
    const box = pricingBox({ accounts, rules: { pricing: { rate: 0.05 } }, session: "2026-11-07" });
    expect(box.id).toBe("pricing");
    expect(box.tabs.map((t) => t.id)).toEqual(["method", "names"]);
    expect(text(box.body("method"))).toContain("5.00%");
    expect(text(box.body("names"))).toContain("2026-11-07 evaluation");
    expect(text(pricingBox(undefined).body("method"))).toContain("the ruleset&#x27;s");
  });
});

const accounts = [account([{ underlying: "XYZ", legs: [leg()] }])];
