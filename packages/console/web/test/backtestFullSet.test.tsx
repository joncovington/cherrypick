import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { FullSetPart, FullSetProgress } from "@console/shared";
import { FullSetBody, PARTS, pct } from "../src/pages/Backtest/FullSetCard";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");
const part = (done: number, total: number, refused = 0): FullSetPart => ({ done, total, refused, share: total ? done / total : null });
const row = (p: FullSetPart) => ({ zero: p, near: p, mid: p, eod: p, oi: p, all: p });

const D: FullSetProgress = {
  ok: true,
  planned: true,
  overall: part(2301, 170823),
  by_part: { zero: part(0, 12096), eod: part(0, 3376), oi: part(2301, 12096, 3), near: part(0, 58240), mid: part(0, 85015) },
  by_symbol: { SPX: row(part(10, 40)), QQQ: row(part(0, 40)) },
  from: "2020-01-02",
  through: "2026-11-16",
};

describe("the full set card", () => {
  it("shows the whole, each part's total, and a row per underlying in the chain's order", () => {
    const out = html(<FullSetBody d={D} />);
    expect(out).toContain("2,301 of 170,823 requests · 2020-01-02 → 2026-11-16");
    expect(out.indexOf(">SPX<")).toBeLessThan(out.indexOf(">QQQ<"));
    expect(out).toContain(">25%<");
    expect(out).toContain("3 refused");
    expect(PARTS.map(([k]) => k)).toEqual(["zero", "eod", "oi", "near", "mid"]);
    expect(out).toContain(">Total<");
  });

  it("writes a small share to a tenth, and an empty plan as a dash", () => {
    expect(pct(part(2301, 170823))).toBe("1.3%");
    expect(pct(part(0, 0))).toBe("—");
    expect(pct(part(1, 4))).toBe("25%");
  });

  it("says how to plan it when nothing is planned", () => {
    expect(html(<FullSetBody d={{ ...D, planned: false }} />)).toContain("fetch_full_set.py plan");
  });
});
