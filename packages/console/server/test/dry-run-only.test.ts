import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildOrderSpec } from "../src/services/staging.js";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

function allSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...allSourceFiles(p));
    else if (entry.name.endsWith(".ts")) out.push(p);
  }
  return out;
}

/**
 * The console is a research/staging surface, never an order-placement one.
 * Ported from scout's test_dry_run_only.py: source-scan the package for any
 * path to a real order, and pin the single dry-run call site.
 */
describe("dry-run-only invariant", () => {
  const files = allSourceFiles(SRC);
  const contents = new Map(files.map((f) => [f, fs.readFileSync(f, "utf-8")]));

  it("no order-placement or order-mutation SDK method is referenced anywhere", () => {
    const forbidden = [
      "createOrder",
      "createComplexOrder",
      "replaceOrder",
      "editOrder",
      "cancelOrder",
      "cancelComplexOrder",
      "postReconfirmOrder",
    ];
    for (const [file, text] of contents) {
      for (const name of forbidden) {
        expect(text.includes(name), `${file} references ${name}`).toBe(false);
      }
    }
  });

  it("postOrderDryRun appears in exactly two files — staging and the scope probe", () => {
    const hits = files.filter((f) => contents.get(f)!.includes("postOrderDryRun"));
    expect(hits.map((h) => path.relative(SRC, h).replace(/\\/g, "/")).sort()).toEqual([
      "auth/probe.ts",
      "services/staging.ts",
    ]);
  });

  it("order specs are opening-only with signed-quantity actions and per-share net price", () => {
    const spec = buildOrderSpec([
      { symbol: "SPXW  260810P07700000", quantity: -1, price: 12.0 },
      { symbol: "SPXW  260810P07650000", quantity: 1, price: 9.5 },
    ]);
    const legs = spec["legs"] as Array<Record<string, unknown>>;
    expect(legs[0]!["action"]).toBe("Sell to Open");
    expect(legs[1]!["action"]).toBe("Buy to Open");
    expect(legs.every((l) => String(l["action"]).endsWith("to Open"))).toBe(true);
    expect(spec["price"]).toBe(2.5);
    expect(spec["price-effect"]).toBe("Credit");
    expect(spec["order-type"]).toBe("Limit");
  });
});
