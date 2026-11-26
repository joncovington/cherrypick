import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageIntentAsk, ManagePmccPosition, ManageTrade } from "@console/shared";
import { PmccBody } from "../src/pages/Live/PmccCards";
import { PmccAccount, PmccIntentChoice, askFor } from "../src/pages/Live/PmccIntent";
import { answer, savedFor } from "../src/pages/Live/intentAnswers";

/**
 * The PMCC card's intent row. The guards: a diagonal manage still asks is offered the choice there, with
 * manage's suggestion and evidence and none of the console's own; an answered one shows what is set and a
 * link to change it on positions that carries the account; the ask is found only on the trade that holds
 * this long, under this long's own key; and a PMCC with no ask keeps the row it had, key included.
 */

const KEY = "XYZ 280121C00040000";

const position = (over: Partial<ManagePmccPosition> = {}) =>
  ({
    underlying: "XYZ",
    intent: null,
    intentSource: null,
    intentKey: KEY,
    tradeIds: ["XYZ-261001-1"],
    recognition: "pmcc",
    recognizedBy: "entered at 0.82",
    spot: 43,
    long: { opened: "2026-10-01" },
    continued: [],
    chainSince: null,
    ...over,
  }) as unknown as ManagePmccPosition;

const ask = (over: Partial<ManageIntentAsk> = {}): ManageIntentAsk => ({
  kind: "pmcc",
  asked: true,
  current: null,
  options: ["pmcc", "diagonal"],
  verdicts: {},
  suggested: "pmcc",
  evidence: ["entered at 0.82"],
  note: null,
  key: `trades.${KEY}.intent`,
  scope: "contract",
  ...over,
});

const trade = (a: ManageIntentAsk | null, tradeId = "XYZ-261001-1") => ({ tradeId, intentAsk: a }) as unknown as ManageTrade;

const row = (p: ManagePmccPosition, trades: ManageTrade[], account: string | null = "****1234") =>
  renderToString(
    <MemoryRouter>
      <PmccAccount.Provider value={account}>
        <PmccBody p={p} trades={trades} tab="recognised" />
      </PmccAccount.Provider>
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");

const text = (h: string) =>
  h
    .replace(/<[^>]+>/g, " ")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();

describe("the PMCC card's intent row", () => {
  it("offers the choice for a diagonal manage still asks, with its suggestion and evidence", () => {
    const h = row(position(), [trade(ask({ evidence: ["its long was entered at a delta of 0.82"] }))]);
    expect(text(h)).toContain("none set");
    expect(h).toContain("PMCC");
    expect(h).toContain("Just a diagonal");
    expect(text(h)).toContain("suggested");
    expect(text(h)).toContain("its long was entered at a delta of 0.82");
    expect((h.match(/class="btn int-strip-opt"/g) ?? []).length).toBe(2);
    // The choice writes the key itself, so the key to copy by hand is not repeated beside it.
    expect(text(h)).not.toContain("trades key");
  });

  it("shows what is set and a link to change it, carrying the account, once it is answered", () => {
    const h = row(position({ intent: "pmcc", intentSource: "contract" }), [trade(ask({ asked: false, current: "pmcc" }))]);
    expect(text(h)).toContain("pmcc, set on this long");
    expect(h).toContain('href="/live/positions?trade=XYZ-261001-1&amp;tab=intent&amp;acct=****1234"');
    expect(h).not.toContain("int-strip-opt");
  });

  it("carries no account where the tab has none", () => {
    const h = row(position({ intent: "pmcc", intentSource: "contract" }), [trade(ask({ asked: false, current: "pmcc" }))], null);
    expect(h).toContain('href="/live/positions?trade=XYZ-261001-1&amp;tab=intent"');
  });

  it("keeps the row it had, key included, where there is no ask", () => {
    const h = text(row(position(), [trade(null)]));
    expect(h).toContain(`none set · trades key "${KEY}"`);
    expect(h).not.toContain("suggested");
  });

  it("offers no write where the answer is not written on a trade", () => {
    const h = row(position(), [trade(ask({ scope: "name", key: "positions.XYZ.intent" }))]);
    expect(h).not.toContain("int-strip-opt");
    // Handed such an ask directly, the choice still shows the words and no button.
    const direct = renderToString(
      <MemoryRouter>
        <PmccIntentChoice
          found={{ ask: ask({ scope: "name", key: "positions.XYZ.intent" }), tradeId: "XYZ-261001-1", provisional: false }}
          words="none set"
          fallback={`none set · trades key "${KEY}"`}
        />
      </MemoryRouter>,
    );
    expect(direct).not.toContain("<button");
    // ...and the key to set by hand, as the row had it.
    expect(text(direct)).toContain(`trades key "${KEY}"`);
  });

  it("keeps the key to set by hand when the question has no options to offer", () => {
    const h = text(row(position(), [trade(ask({ options: [] }))]));
    expect(h).toContain(`none set · trades key "${KEY}"`);
    expect(h).not.toContain("suggested");
  });

  it("says there is no trade ID to open while the trade's ID is provisional", () => {
    const provisional = { ...trade(ask({ asked: false, current: "pmcc" })), tradeIdProvisional: true } as ManageTrade;
    const h = row(position({ intent: "pmcc", intentSource: "contract" }), [provisional]);
    expect(h).not.toContain("/live/positions");
    expect(text(h)).toContain("no trade ID yet");
  });
});

describe("finding the question", () => {
  it("is the ask under this long's own key on a trade this PMCC holds", () => {
    expect(askFor(position(), [trade(ask())])).toEqual({ ask: ask(), tradeId: "XYZ-261001-1" });
  });

  it("compares the key as manage does: spaces and case ignored", () => {
    const padded = trade(ask({ key: "trades.xyz   280121c00040000.intent" }));
    expect(askFor(position(), [padded])?.tradeId).toBe("XYZ-261001-1");
  });

  it("shares its saved state with the strip and the tab, under the spaced contract key", async () => {
    // The row, the strip and the tab all write and read the key as the ask spells it, spaces kept.
    const a = ask();
    const sent: unknown[] = [];
    await answer(KEY, a, "pmcc", async (_url, _method, body) => {
      sent.push(body);
      return { intent: "pmcc", previous: null };
    });
    expect(sent).toEqual([{ trade: KEY, intent: "pmcc" }]);
    expect(savedFor(KEY, a).saved).toEqual({ intent: "pmcc", previous: null });
  });

  it("is not another long's, another PMCC's trade, or a missing key", () => {
    expect(askFor(position(), [trade(ask({ key: "trades.XYZ 280121C00045000.intent" }))])).toBeNull();
    expect(askFor(position(), [trade(ask(), "OTHER-261001-1")])).toBeNull();
    expect(askFor(position({ intentKey: null }), [trade(ask())])).toBeNull();
    expect(askFor(position(), [])).toBeNull();
  });
});
