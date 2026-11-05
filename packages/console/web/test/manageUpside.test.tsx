import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { Upside } from "../src/pages/Live/manageParts";

/**
 * "What it can still do" (2026-11-04): left to win is green when there is something to win and neutral
 * otherwise. It is never red, since red is for what a trade can lose. Synthetic figures.
 */
describe("left to win", () => {
  it("is green above zero, neutral at or under it, muted with no figure", () => {
    expect(renderToString(<Upside v={120} />)).toContain('class="pnl-pos"');
    for (const v of [0, -45]) {
      const html = renderToString(<Upside v={v} />);
      expect(html).not.toContain("pnl-neg");
      expect(html).not.toContain("pnl-pos");
    }
    expect(renderToString(<Upside v={null} />)).toContain('class="muted"');
  });
});
