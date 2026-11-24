import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { LiveTodayPayload } from "@console/shared";
import { AttentionChip, kindWords } from "../src/pages/Live/AttentionChip";

/**
 * The loops card's attention chip: a plain label at zero, a button with items, and its list (opened
 * here with `initiallyOpen`, as the server render cannot click) naming each loop and condition with a way
 * to it. The click, Escape and the scroll are checked in a browser.
 */

const ITEMS: LiveTodayPayload["attention"] = [
  { loop: "meic", kind: "daily_loss_breaker", detail: "daily-loss breaker tripped: -412 against a 400 limit; no new entries today", to: "/live/meic" },
  { loop: "ladder", kind: "held_submission", detail: "1 held submission: no broker answer, or a fill the ledger never recorded", to: "/live/ladder" },
];

const render = (items: LiveTodayPayload["attention"], hasCard: (l: string) => boolean, initiallyOpen = false) =>
  renderToString(
    <MemoryRouter>
      <AttentionChip items={items} hasCard={hasCard} onGo={() => {}} initiallyOpen={initiallyOpen} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");

describe("AttentionChip", () => {
  it("is a plain label at zero: no button, nothing to open", () => {
    const html = render([], () => true);
    expect(html).toContain("needs attention · 0");
    expect(html).not.toContain("<button");
  });

  it("is a closed button with the count when there are items", () => {
    const html = render(ITEMS, () => true);
    expect(html).toContain("<button");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("needs attention · 2");
    expect(html).not.toContain("held submission");
  });

  it("lists each item with its loop, condition and the loop's own sentence when open, in the order it was given", () => {
    const html = render(ITEMS, () => true, true);
    expect(html).toContain('aria-expanded="true"');
    expect(html.indexOf("daily loss breaker")).toBeGreaterThan(-1);
    expect(html.indexOf("daily loss breaker")).toBeLessThan(html.indexOf("held submission"));
    expect(html).toContain("1 held submission: no broker answer, or a fill the ledger never recorded");
    expect(html).toContain("Go to meic →");
  });

  it("scrolls to a loop's card when it has one, and links to the loop's own page when it has none", () => {
    const withCard = render(ITEMS, () => true, true);
    expect(withCard).not.toContain('href="/live/meic"');
    const without = render(ITEMS, (l) => l !== "meic", true);
    expect(without).toContain('href="/live/meic"'); // an off module has no card on the page
    expect(without).not.toContain('href="/live/ladder"');
  });

  it("words a kind with spaces", () => {
    expect(kindWords("overdue_settlement")).toBe("overdue settlement");
  });
});
