import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
 * The console reads; it never places an order.
 *
 * Ported from scout's test_dry_run_only.py, and NARROWED on 2026-08-31 rather than retired with the
 * research section. Order staging was the console's only path that touched an order at all, and it
 * went with the builder -- so `postOrderDryRun` now appears in exactly ONE file, the scope probe,
 * which uses it to ask the broker what the token may do and never to describe a trade.
 *
 * The invariant got STRONGER as the surface shrank, which is the reason to narrow this rather than
 * delete it alongside what it was watching: it now asserts that nothing has grown back. The
 * order-spec case went with `buildOrderSpec`; there is no longer an order spec to shape.
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

  it("postOrderDryRun appears in exactly one file — the scope probe", () => {
    const hits = files.filter((f) => contents.get(f)!.includes("postOrderDryRun"));
    expect(hits.map((h) => path.relative(SRC, h).replace(/\\/g, "/")).sort()).toEqual([
      "auth/probe.ts",
    ]);
  });
});
