import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManagePayload, ManageTrade } from "@console/shared";
import { BrokerSummaryCard, WHAT_TO_DO_HELP, actionChip, brokerSummary } from "../src/pages/Live/WhatToDo";

/**
 * The broker positions card: a summary of the position evaluation. Every action, reason and net is
 * manage's own and the order is manage's urgency order; nothing is re-judged or re-ranked, and the card
 * never claims "nothing to do" for positions it has not read.
 */

const trade = (over: Partial<ManageTrade>) =>
  ({
    tradeId: "XYZ",
    tradeIdProvisional: false,
    mergedIds: [],
    action: "HOLD",
    reason: "no rule fired",
    pnlNet: null,
    nearestShortDte: 30,
    nearestShortExpiry: "2026-11-30",
    earnings: null,
    ...over,
  }) as unknown as ManageTrade;

type Acct = { account: string | null; trades: ManageTrade[]; fits?: number };

const manage = (...accounts: Acct[]) =>
  ({
    session: "2026-10-30",
    accounts: accounts.map((a) => ({ account: a.account, trades: a.trades, portfolio: { candidates: { fitsTotal: a.fits ?? 0 } } })),
  }) as unknown as ManagePayload;

const one = (trades: ManageTrade[], fits = 0) => manage({ account: "****0000", trades, fits });

describe("brokerSummary", () => {
  it("counts the trades to act on, the shorts expiring within a week and the reports before expiry, once each", () => {
    const s = brokerSummary(
      one(
        [
          trade({ tradeId: "AAA", action: "CLOSE", reason: "21 DTE; losing, so close" }),
          trade({ tradeId: "BBB", action: "REVIEW", reason: "net debit is over the width" }),
          trade({ tradeId: "CCC" }),
          trade({ tradeId: "DDD", nearestShortDte: 3 }),
          trade({ tradeId: "EEE", earnings: { date: "2026-11-05", days: 6, timeOfDay: "AMC", beforeNearestShortExpiry: true } as ManageTrade["earnings"] }),
          trade({ tradeId: "FFF", earnings: { date: "2026-12-20", days: 51, timeOfDay: null, beforeNearestShortExpiry: false } as ManageTrade["earnings"] }),
        ],
        4,
      ),
    );
    expect([s.act, s.expiring, s.earnings, s.income]).toEqual([2, 1, 1, 4]);
  });

  it("names the first three in manage's urgency order, across accounts, and carries manage's own reason and net", () => {
    const s = brokerSummary(
      manage(
        {
          account: "****1111",
          trades: [
            trade({ tradeId: "REV", action: "REVIEW" }),
            trade({ tradeId: "CL1", action: "CLOSE", reason: "loss has reached the stop", pnlNet: -294.28 }),
            trade({ tradeId: "LEG", action: "CLOSE_LEGS" }),
          ],
        },
        { account: "margin ****2222", trades: [trade({ tradeId: "ROL", action: "ROLL_FOR_CREDIT" }), trade({ tradeId: "CL2", action: "CLOSE" })] },
      ),
    );
    // roll for credit ranks above close in manage's own order, so the roll leads, though it is in the second account
    expect(s.urgent.map((u) => u.tradeId)).toEqual(["ROL", "CL1", "CL2"]);
    expect(s.act).toBe(5);
    const cl1 = s.urgent[1]!;
    expect([cl1.reason, cl1.net, cl1.account]).toEqual(["loss has reached the stop", -294.28, "****1111"]);
  });

  it("names the account only when the evaluation covers several", () => {
    const s = brokerSummary(one([trade({ tradeId: "AAA", action: "CLOSE" })]));
    expect(s.urgent[0]!.account).toBeNull();
  });

  it("reads nothing when the evaluation has not been read", () => {
    expect(brokerSummary(undefined)).toEqual({ act: 0, expiring: 0, earnings: 0, income: 0, urgent: [] });
  });
});

describe("actionChip", () => {
  it("words manage's action and tones it: an exit red, a profit green, a review blue, the rest amber", () => {
    expect(actionChip("CLOSE")).toEqual({ text: "close", cls: "chip-bad" });
    expect(actionChip("CLOSE_LEGS").cls).toBe("chip-bad");
    expect(actionChip("TAKE_PROFIT").cls).toBe("chip-ok");
    expect(actionChip("REVIEW").cls).toBe("chip-hold");
    expect(actionChip("ROLL_FOR_CREDIT")).toEqual({ text: "roll", cls: "chip-warn" });
  });
});

describe("BrokerSummaryCard", () => {
  const render = (m: ManagePayload | undefined) =>
    renderToString(
      <MemoryRouter>
        <BrokerSummaryCard manage={m} />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");

  it("never says there is nothing to do on positions it has not read", () => {
    const unread = render(undefined);
    expect(unread).toContain("has not been read");
    expect(unread).not.toContain("No position has an action");
    expect(render(one([]))).toContain("No position has an action");
  });

  it("shows the counts and the first three, with manage's net, and a way into Positions", () => {
    const html = render(
      one(
        [
          trade({ tradeId: "AAA", action: "CLOSE", reason: "loss has reached the stop", pnlNet: -294.28 }),
          trade({ tradeId: "BBB", action: "REVIEW" }),
          trade({ tradeId: "CCC", action: "REVIEW" }),
          trade({ tradeId: "DDD", action: "REVIEW" }),
        ],
        2,
      ),
    );
    expect(html).toContain("broker positions");
    expect(html).toContain("as evaluated 2026-10-30");
    expect(html).toContain("loss has reached the stop");
    expect(html).toContain("-$294.28");
    expect(html).toContain("1 more to act on, in the same order");
    expect(html).toContain("All 4 on Positions →");
    expect(html).toContain('href="/live/positions"');
    expect(html).toContain('href="/live/portfolio"'); // the income tile
    expect(html).not.toContain("DDD"); // the fourth is on Positions
  });

  it("says it is advice only, behind the card's ? rather than in its body", () => {
    expect(render(one([trade({ action: "CLOSE" })]))).not.toContain("places or changes an order");
    expect(renderToString(<>{WHAT_TO_DO_HELP.body}</>)).toContain("nothing on this card places or changes an order");
  });
});
