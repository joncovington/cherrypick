import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageAccount, ManageAttentionIntent } from "@console/shared";
import { DiagonalIntentLook, intentLookLink } from "../src/pages/Live/IntentLook";
import { PmccCards } from "../src/pages/Live/PmccCards";

/**
 * Live → pmcc's needs a look: the call diagonals manage still asks whether they are a PMCC. The guards: one
 * row per open question with manage's suggestion and why; a link to the trade's Intent tab that carries the
 * account; no link where there is no trade to open; puts are not listed here (the wheel tab has them); the
 * section is left out when nothing is asked, and on an evaluation written before the list; and it sits on
 * the tab whether or not any PMCC was recognised.
 */

const item = (over: Partial<ManageAttentionIntent> = {}): ManageAttentionIntent => ({
  tradeId: "ABC-261001-1",
  underlying: "ABC",
  kind: "pmcc",
  shape: "diagonal",
  legs: ["+1 2028-01-21 40C", "-1 2026-12-18 55C"],
  scope: "contract",
  suggested: "diagonal",
  evidence: ["entered at a delta under 0.70"],
  note: null,
  ...over,
});

const account = (items: ManageAttentionIntent[] | undefined, pmcc: unknown = null): ManageAccount =>
  ({
    account: "****1234",
    attention: { nearPct: 0.03, expiringDays: 7, items: [], expiring: [], ...(items === undefined ? {} : { intent: items }) },
    pmcc,
    trades: [],
  }) as unknown as ManageAccount;

const raw = (a: ManageAccount) =>
  renderToString(
    <MemoryRouter>
      <DiagonalIntentLook a={a} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");

const text = (h: string) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("the diagonals asked", () => {
  it("lists each with its legs, manage's suggestion and why", () => {
    const t = text(raw(account([item(), item({ tradeId: "DEF-261001-1", underlying: "DEF", suggested: null, evidence: [] })])));
    expect(t).toContain("Which diagonals ask if they are a PMCC?");
    expect(t).toContain("ABC +1 2028-01-21 40C, -1 2026-12-18 55C");
    expect(t).toContain("looks like: Just a diagonal (entered at a delta under 0.70)");
    expect(t).toContain("DEF");
    expect(t).toContain("no suggestion");
  });

  it("links to the trade's Intent tab, carrying the account", () => {
    expect(intentLookLink(item(), "****1234")).toBe("/live/positions?trade=ABC-261001-1&tab=intent&acct=****1234");
    expect(intentLookLink(item(), null)).toBe("/live/positions?trade=ABC-261001-1&tab=intent");
    expect(raw(account([item()]))).toContain('href="/live/positions?trade=ABC-261001-1&amp;tab=intent&amp;acct=****1234"');
  });

  it("links nowhere where there is no trade to open", () => {
    expect(intentLookLink(item({ tradeId: null }), "****1234")).toBeNull();
    expect(intentLookLink(item({ scope: "name" }), "****1234")).toBeNull();
    expect(raw(account([item({ tradeId: null })]))).not.toContain("<a ");
  });

  it("does not list a put, which the wheel tab has", () => {
    expect(raw(account([item({ kind: "wheel", scope: "trade", underlying: "PUT" })]))).toBe("");
  });

  it("is left out when nothing is asked, and on an evaluation written before the list", () => {
    expect(raw(account([]))).toBe("");
    expect(raw(account(undefined))).toBe("");
  });
});

describe("on the PMCC tab", () => {
  const tab = (a: ManageAccount) =>
    text(
      renderToString(
        <MemoryRouter>
          <PmccCards a={a} />
        </MemoryRouter>,
      ),
    );
  const view = { positions: [], recognize: null, error: null, underlyingHistoryError: null, bandsMissing: null };

  it("shows the question even when no PMCC was recognised", () => {
    expect(tab(account([item()], view))).toContain("Which diagonals ask if they are a PMCC?");
    expect(tab(account([], view))).not.toContain("Which diagonals ask");
  });

  it("gives the section its own layout where no PMCC was recognised, and none when nothing is asked", () => {
    const html = (a: ManageAccount) =>
      renderToString(
        <MemoryRouter>
          <PmccCards a={a} />
        </MemoryRouter>,
      );
    expect(html(account([item()], view))).toMatch(/<div class="pf-layout"><section[^>]*aria-label="needs a look"/);
    expect(html(account([], view))).not.toContain("pf-layout");
  });
});
