import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { LiveTodayPayload, ManagePayload, ManageTrade } from "@console/shared";
import { WhatToDoList, todoList } from "../src/pages/Live/WhatToDo";

/**
 * What to do today (2026-10-30, docs/console-review-plan.md batch 5): one list from the live loops'
 * attention items and the position evaluation. Every action and reason is manage's own; nothing is
 * re-judged, and the list never claims "nothing to do" for positions it has not read.
 */

const trade = (over: Partial<ManageTrade>) =>
  ({
    tradeId: "XYZ",
    action: "HOLD",
    reason: "no rule fired",
    nearestShortDte: 30,
    nearestShortExpiry: "2026-11-30",
    earnings: null,
    ...over,
  }) as unknown as ManageTrade;

const manage = (trades: ManageTrade[], fits = 0, account: string | null = "****0000") =>
  ({
    session: "2026-10-30",
    accounts: [{ account, trades, portfolio: { candidates: { fitsTotal: fits } } }],
  }) as unknown as ManagePayload;

const live = (attention: LiveTodayPayload["attention"] = []) => ({ attention }) as unknown as LiveTodayPayload;

describe("todoList", () => {
  it("leads with a live loop that needs a person, then manage's actions in its own urgency order", () => {
    const items = todoList(
      live([{ loop: "meic", kind: "orphaned_orders", detail: "1 working broker order", to: "/live/meic" }]),
      manage([
        trade({ tradeId: "BBB", action: "REVIEW", reason: "net debit is over the width" }),
        trade({ tradeId: "AAA", action: "CLOSE", reason: "21 DTE; losing, so close" }),
        trade({ tradeId: "CCC" }),
      ]),
    );
    expect(items.map((i) => [i.group, i.what])).toEqual([
      ["person", "meic: orphaned orders"],
      ["act", "AAA: close"],
      ["act", "BBB: review"],
    ]);
    expect(items[1]!.detail).toBe("21 DTE; losing, so close"); // the reason is manage's, verbatim
  });

  it("lists a held position expiring within a week, or reporting before its short expires, once each", () => {
    const items = todoList(
      live(),
      manage([
        trade({ tradeId: "DDD", nearestShortDte: 3 }),
        trade({ tradeId: "EEE", earnings: { date: "2026-11-05", days: 6, timeOfDay: "AMC", beforeNearestShortExpiry: true } as ManageTrade["earnings"] }),
        trade({ tradeId: "FFF", earnings: { date: "2026-12-20", days: 51, timeOfDay: null, beforeNearestShortExpiry: false } as ManageTrade["earnings"] }),
      ]),
    );
    expect(items.map((i) => [i.group, i.what])).toEqual([
      ["expiring", "DDD: short expires in 3d"],
      ["earnings", "EEE: reports 2026-11-05"],
    ]);
  });

  it("counts the income trades that fit, and says they are not ranked", () => {
    const [item] = todoList(live(), manage([], 4));
    expect(item!.what).toBe("4 income trades fit the account's room");
    expect(item!.detail).toContain("not ranked");
  });
});

describe("WhatToDoList", () => {
  const render = (items: ReturnType<typeof todoList>, evaluated: string | null) =>
    renderToString(
      <MemoryRouter>
        <WhatToDoList items={items} evaluated={evaluated} />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");

  it("never says there is nothing to do on positions it has not read", () => {
    const unread = render([], null);
    expect(unread).toContain("has not been read");
    expect(unread).not.toContain("no position has an action");
    expect(render([], "2026-10-30")).toContain("no position has an action");
  });

  it("says it is advice only", () => {
    expect(render([], "2026-10-30")).toContain("nothing on this list places or changes an order");
  });
});
