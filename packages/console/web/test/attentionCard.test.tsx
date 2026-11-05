import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageAttention, ManageAttentionItem } from "@console/shared";
import { AttentionCard, meterFill } from "../src/pages/Live/AttentionCard";

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

describe("needs a look", () => {
  it("lists manage's items with their level and why, worst first as manage ordered them", () => {
    const out = text(
      renderToString(
        <AttentionCard a={attention({ items: [item({ underlying: "BBB", level: "in the money", why: "3.0% through the strike", distance: -0.03 }), item()] })} />,
      ),
    );
    expect(out).toContain("BBB 2026-12-18 49P 3.0% through the strike");
    expect(out.indexOf("BBB")).toBeLessThan(out.indexOf("AAA"));
    expect(out).toContain("spot 50.00 · time value 0.90");
    expect(out).toContain("a short within 3% of its strike");
  });

  it("on the wheel tab keeps only the wheel's names, and says so", () => {
    const a = attention({
      items: [item({ underlying: "BBB" }), item()],
      expiring: [item({ underlying: "CCC", leg: "2026-11-06 30P", dte: 1 }), item({ underlying: "AAA", leg: "2026-11-06 47P", dte: 1 })],
    });
    const out = text(renderToString(<AttentionCard a={a} scope={["AAA"]} />));
    expect(out).not.toContain("BBB");
    expect(out).not.toContain("CCC");
    expect(out).toContain("AAA 2026-12-18 49P");
    expect(out).toContain("AAA −1 2026-11-06 47P");
    expect(out).toContain("a short on a wheel name within 3%");
    const none = text(renderToString(<AttentionCard a={a} scope={[]} />));
    expect(none).toContain("nothing: every short option on a wheel name is further than 3%");
    expect(none).toContain("nothing on a wheel name expires in 7 days");
  });

  it("shows manage's level even where its distance would say otherwise", () => {
    const out = text(renderToString(<AttentionCard a={attention({ items: [item({ distance: 0.2, level: "early exercise", why: "manage says so" })] })} />));
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
    const out = text(
      renderToString(
        <AttentionCard
          a={attention({
            items: [],
            expiring: [{ ...item(), leg: "2026-11-06 45P", expiration: "2026-11-06", dte: 2, quantity: -1, distance: 0.08 }],
          })}
        />,
      ),
    );
    expect(out).toContain("nothing: every short option is further than 3% from its strike");
    expect(out).toContain("Expiring in 7 days");
    expect(out).toContain("AAA −1 2026-11-06 45P · 2 d · 8.0% from the strike");
  });
});
