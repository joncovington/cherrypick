import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ActionBlock } from "../src/components/ActionBlock";
import { panelPlacement } from "../src/components/FilterSort";

/**
 * The suite redesign's shared parts (2026-11-06): the filter & sort panel stays on screen, opening
 * above its button when there is no room below; a trade that needs action says the rule, its
 * confidence and why, with the proposed order under it.
 */

describe("the filter & sort panel's place", () => {
  const button = (top: number) => ({ top, bottom: top + 28, right: 1200 });

  it("opens under its button when it fits", () => {
    const p = panelPlacement(button(100), 400, 1440, 900);
    expect(p.top).toBe(134);
    expect(p.maxHeight).toBe(900 - 128 - 12);
  });

  it("opens above its button near the foot of the screen, never taller than the room above", () => {
    const p = panelPlacement(button(800), 400, 1440, 900);
    expect(p.top).toBe(800 - 6 - 400);
    expect(p.top + 400).toBeLessThanOrEqual(800);
    const tall = panelPlacement(button(300), 900, 1440, 400);
    expect(tall.top).toBeGreaterThanOrEqual(0);
    expect(tall.maxHeight).toBe(300 - 12);
  });

  it("stays inside the screen's width", () => {
    expect(panelPlacement({ top: 100, bottom: 128, right: 200 }, 300, 390, 844).left).toBe(12);
    expect(panelPlacement({ top: 100, bottom: 128, right: 1430 }, 300, 1440, 900).left).toBe(1440 - 380 - 12);
  });
});

describe("the action block", () => {
  it("names the rule, its confidence and why, with the proposed order under it", () => {
    const html = renderToString(
      <ActionBlock rule="short tested" confidence={60} reason="0.6% in the money with $0.18 of time value left">
        <span>the roll</span>
      </ActionBlock>,
    ).replace(/<!-- -->/g, "");
    expect(html).toContain('aria-label="Needs action: short tested"');
    expect(html).toContain('<span class="chip chip-warn">short tested</span>');
    expect(html).toContain("confidence 60");
    expect(html).toContain("0.6% in the money");
    expect(html).toContain("<span>the roll</span>");
    expect(renderToString(<ActionBlock rule="close" reason="stop hit" />)).not.toContain("confidence");
  });
});
