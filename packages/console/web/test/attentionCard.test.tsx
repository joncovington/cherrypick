import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageAttention, ManageAttentionItem, ManageCostCallRow } from "@console/shared";
import { CostCallCard, ExpiringCard, NearStrikeCard, costCallRows, distanceWords, levelLabel, meterFill } from "../src/pages/Live/AttentionCard";

/**
 * Live → wheel's "needs a look" draws manage's `attention` and decides nothing: the level and the why
 * are manage's (a level its distance would not give is still shown as manage wrote it), the meter is
 * full at or through the strike and empty at the alert distance, and an empty list says so plainly.
 * Synthetic names and figures.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function item(over: Partial<ManageAttentionItem> = {}): ManageAttentionItem {
  return {
    underlying: "AAA",
    symbol: "AAA   261218P00049000",
    leg: "2026-12-18 49P",
    right: "P",
    strike: 49,
    expiration: "2026-12-18",
    dte: 44,
    quantity: -1,
    spot: 50,
    distance: 0.0204,
    mid: 0.9,
    extrinsic: 0.9,
    level: "near",
    why: "2.0% from the strike (alert at 3%)",
    ...over,
  };
}

function attention(over: Partial<ManageAttention> = {}): ManageAttention {
  return { nearPct: 0.03, expiringDays: 7, items: [item()], expiring: [], ...over };
}

function both(a: ManageAttention, scope: string[] | null = null): string {
  return text(
    renderToString(
      <>
        <NearStrikeCard a={a} scope={scope} />
        <ExpiringCard a={a} scope={scope} />
      </>,
    ),
  );
}

describe("needs a look", () => {
  it("lists manage's items, worst first as manage ordered them; a breach reads breached, its line ITM", () => {
    const out = both(attention({ items: [item({ underlying: "BBB", level: "in the money", why: "3.0% through the strike", distance: -0.03 }), item()] }));
    expect(out).toContain("BBB 2026-12-18 49P · ITM breached");
    expect(out).toContain("AAA 2026-12-18 49P · 2.0% away near");
    expect(out.indexOf("BBB")).toBeLessThan(out.indexOf("AAA"));
    expect(out).toContain("spot 50.00 · time value 0.90");
    // The chip never says "through": that word was the old label.
    expect(out).not.toContain("through");
    expect(levelLabel("in the money")).toBe("breached");
    expect(levelLabel("near")).toBe("near");
    expect(distanceWords(0)).toBe("ITM");
    expect(distanceWords(0.012)).toBe("1.2% away");
  });

  it("on the wheel tab keeps only the wheel's names, and says so", () => {
    const a = attention({
      items: [item({ underlying: "BBB" }), item()],
      expiring: [item({ underlying: "CCC", leg: "2026-11-06 30P", dte: 1 }), item({ underlying: "AAA", leg: "2026-11-06 47P", dte: 1 })],
    });
    const out = both(a, ["AAA"]);
    expect(out).not.toContain("BBB");
    expect(out).not.toContain("CCC");
    expect(out).toContain("AAA 2026-12-18 49P");
    expect(out).toContain("AAA −1 2026-11-06 47P");
    const none = both(a, []);
    expect(none).toContain("nothing: every short option on a wheel name is further than 3% from its strike");
    expect(none).toContain("nothing on a wheel name expires in 7 days");
  });

  it("shows manage's level even where its distance would say otherwise, with its why", () => {
    const out = both(attention({ items: [item({ distance: 0.2, level: "early exercise", why: "manage says so" })] }));
    expect(out).toContain("early exercise");
    expect(out).toContain("manage says so");
  });

  it("fills the meter at the strike and empties it at the alert distance", () => {
    expect(meterFill(-0.01, 0.03)).toBe(100);
    expect(meterFill(0, 0.03)).toBe(100);
    expect(meterFill(0.015, 0.03)).toBeCloseTo(50);
    expect(meterFill(0.03, 0.03)).toBe(0);
    expect(meterFill(null, 0.03)).toBe(0);
  });

  it("says plainly when nothing needs a look, and lists what expires", () => {
    const out = both(
      attention({
        items: [],
        expiring: [{ ...item(), leg: "2026-11-06 45P", expiration: "2026-11-06", dte: 2, quantity: -1, distance: 0.08 }],
      }),
    );
    expect(out).toContain("nothing: every short option is further than 3% from its strike");
    expect(out).toContain("expiring in 7 days");
    expect(out).toContain("AAA −1 2026-11-06 45P · 2d 8.0%");
  });
});

function costRow(over: Partial<ManageCostCallRow> = {}): ManageCostCallRow {
  return {
    underlying: "AAA",
    shares: 100,
    contracts: 1,
    effectiveCost: 49.06,
    spot: 49,
    minBidPct: 0.02,
    expiration: "2026-11-20",
    dte: 38,
    window: "standard",
    strike: 50,
    bid: 1.2,
    ask: 1.3,
    bidPct: 1.2 / 49,
    credit: 120,
    source: "stream cache",
    status: "pays",
    why: "the 2026-11-20 50 call bids 1.20, 2.4% of 49.00 (at or over the 2% bar)",
    ...over,
  };
}

function costCard(rows: ManageCostCallRow[], scope: string[] | null = null, enabled = true): string {
  const a = attention({ costCall: { enabled, minBidPct: 0.02, rows, takenAt: null } });
  return text(renderToString(<CostCallCard a={a} scope={scope} />));
}

describe("the cost-strike call", () => {
  it("flags a call that bids the bar, with its strike, expiry, bid and bid as a share of the price", () => {
    const out = costCard([costRow()]);
    expect(out).toContain("AAA · 100 sh · cost 49.06 bids 2.4%");
    expect(out).toContain("2026-11-20 50C · 38d · bid 1.20 on 49.00 = 2.4% (bar 2%) · +$120.00 for 1 contract · stream cache");
  });

  it("shows a bid under the bar quietly, and a missing quote as no quote with why, never as under the bar", () => {
    const out = costCard([
      costRow({ underlying: "BBB", status: "below", bid: 0.4, bidPct: 0.0082, credit: 40 }),
      costRow({ underlying: "CCC", status: "no quote", bid: null, bidPct: null, credit: null, source: null, why: "no bid for the 2026-11-20 50C" }),
    ]);
    expect(out).toContain("BBB · 100 sh · cost 49.06 below");
    expect(out).toContain("CCC · 100 sh · cost 49.06 no quote");
    expect(out).toContain("no bid for the 2026-11-20 50C");
    expect(out).not.toMatch(/CCC[^]*(doesn't pay|does not pay|below)/);
  });

  it("names a nearest-monthly fallback, keeps to the wheel's names, and says plainly when there is nothing", () => {
    expect(costCard([costRow({ window: "fallback", dte: 54 })])).toContain("· 54d (nearest monthly)");
    expect(costCard([costRow({ underlying: "ZZZ" })], ["AAA"])).toContain("no wheel cycle holds assigned shares without a call written");
    expect(costCallRows(attention({ costCall: { enabled: false, minBidPct: 0.02, rows: [costRow()], takenAt: null } }))).toEqual([]);
    expect(costCallRows(attention())).toEqual([]);
  });
});
