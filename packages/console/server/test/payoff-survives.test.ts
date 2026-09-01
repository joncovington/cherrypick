import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { payoffAt, type Leg } from "../src/analytics/payoff.js";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

/**
 * `analytics/payoff.ts` sat among the research/screener code retired on 2026-08-31, and three of its
 * four consumers went with that section (`/api/payoff`, the builder, the screener). The fourth is
 * `readers/meic.ts`, which uses `payoffAt` for the profit-forest curves.
 *
 * Deleting it with the rest would not have failed a build in any obvious place — MEIC's page would
 * still render, with an empty forest. This pins the dependency so the next cleanup pass cannot make
 * that mistake by reading the directory rather than the imports.
 */
describe("the payoff engine survives the research teardown", () => {
  it("is still imported by a MODULE reader, not only by research code", () => {
    const meic = fs.readFileSync(path.join(SRC, "readers", "meic.ts"), "utf-8");
    expect(meic).toContain("analytics/payoff.js");
    expect(meic).toContain("payoffAt");
  });

  it("still computes, so the forest is not silently empty", () => {
    // A 6000/6010 call credit spread, quantity signed and priced per contract: $2.00 net credit
    // below both strikes, $8.00 net loss above both, x100.
    const legs: Leg[] = [
      { kind: "call", strike: 6000, quantity: -1, price: 3.0 },
      { kind: "call", strike: 6010, quantity: 1, price: 1.0 },
    ];
    expect(payoffAt(legs, 5900)).toBeCloseTo(200, 5);
    expect(payoffAt(legs, 6100)).toBeCloseTo(-800, 5);
  });

  it("has no imports, so nothing in the retired section can drag it out", () => {
    const src = fs.readFileSync(path.join(SRC, "analytics", "payoff.ts"), "utf-8");
    expect(src.match(/^import /m)).toBeNull();
  });
});
