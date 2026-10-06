import { describe, expect, it } from "vitest";
import { MODULE_LINKS } from "../src/components/shell/navLinks";
import { TRADING_MODULE_ORDER } from "../src/lightbox/moduleOrder";

/**
 * The header menu and the rail read `MODULE_LINKS`, a hand-kept list; the routes read
 * `TRADING_MODULE_ORDER`. contango shipped 2026-10-05 in the second and not the first: its pages
 * rendered at /contango and nothing linked to them. Driven off the module order, so the next
 * module added there fails here until it has a link.
 */
describe("module nav links", () => {
  it("links every trading module, in the suite's order", () => {
    const linked = MODULE_LINKS.map((l) => l.to.slice(1)).filter((id) => id !== "gex");
    expect(linked).toEqual([...TRADING_MODULE_ORDER]);
  });

  it("gives every link a distinct shortcut", () => {
    const keys = MODULE_LINKS.map((l) => l.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
