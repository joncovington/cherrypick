import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { IntentTab } from "../src/pages/Live/IntentTab";

/**
 * The opened trade's Intent tab: each answer beside the verdict the trade would get under it, what is held
 * now, and where the answer is written. The guards: the verdicts and the suggestion are manage's and are all
 * shown; the current answer is marked and not offered again; a put answered on its name gets the comparison
 * but no write; a trade manage does not ask says so; and an ask with no usable key offers no write.
 */

const PUT: ManageIntentAsk = {
  kind: "wheel",
  asked: true,
  current: null,
  options: ["wheel", "no_wheel"],
  verdicts: {
    wheel: {
      action: "HOLD",
      rule: null,
      reason: "a wheel keeps the put to assignment",
    },
    no_wheel: {
      action: "ROLL",
      rule: "roll.naked_put",
      reason: "tested, with time left",
    },
  },
  suggested: "wheel",
  evidence: ["2 completed wheel cycles on XYZ"],
  note: null,
  key: "trades.XYZ-261001-1.intent",
  scope: "trade",
};

const trade = (ask: ManageIntentAsk | null): ManageTrade =>
  ({ tradeId: "XYZ-261001-1", intentAsk: ask }) as unknown as ManageTrade;

const html = (ask: ManageIntentAsk | null) =>
  renderToString(
    <MemoryRouter>
      <IntentTab t={trade(ask)} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");

const text = (h: string) =>
  h
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
const buttons = (h: string) => (h.match(/<button/g) ?? []).length;

describe("the Intent tab", () => {
  it("shows every answer with manage's verdict, rule and reason, and its suggestion", () => {
    const t = text(html(PUT));
    expect(t).toContain("Is this put a wheel?");
    expect(t).toContain("No intent yet.");
    expect(t).toContain("Wheel suggested");
    expect(t).toContain("hold");
    expect(t).toContain("a wheel keeps the put to assignment");
    expect(t).toContain("Not a wheel");
    expect(t).toContain("roll · roll.naked_put");
    expect(t).toContain("tested, with time left");
    expect(t).toContain("2 completed wheel cycles on XYZ");
  });

  it("offers each answer once, and says where it is written", () => {
    const h = html(PUT);
    expect(buttons(h)).toBe(2);
    expect(h).toContain("<code>trades.XYZ-261001-1.intent</code>");
  });

  it("marks the answer held now and does not offer it again", () => {
    const h = html({ ...PUT, asked: false, current: "wheel" });
    expect(text(h)).toContain("Answered: Wheel");
    expect(h).toContain('aria-pressed="true"');
    expect(h).toMatch(/<button[^>]*disabled=""[^>]*aria-pressed="true"/);
    expect(h).toContain('aria-pressed="false"'); // the other answer is still open
  });

  it("shows a diagonal's answers with no verdicts", () => {
    const t = text(
      html({
        ...PUT,
        kind: "pmcc",
        options: ["pmcc", "diagonal"],
        verdicts: {},
        suggested: null,
        evidence: [],
        key: "trades.XYZ 280121C00040000.intent",
        scope: "contract",
      }),
    );
    expect(t).toContain("Is this diagonal a PMCC?");
    expect(t).toContain("PMCC");
    expect(t).toContain("Just a diagonal");
  });

  it("shows a put answered on its name the comparison and no write, and points to Config", () => {
    const h = html({ ...PUT, scope: "name", key: "positions.XYZ.intent" });
    expect(buttons(h)).toBe(0);
    expect(text(h)).toContain("a wheel keeps the put to assignment");
    expect(h).toContain('href="/config/rules"');
  });

  it("does not send a put to Config while the name's intent is for another trade on it", () => {
    const h = html({ ...PUT, scope: "name", key: "positions.XYZ.intent", note: "the name's intent 'pmcc' is for another trade on XYZ, not this put" });
    expect(h).not.toContain("/config/rules");
    expect(text(h)).toContain("wait for a stable ID");
  });

  it("offers no write where the key cannot be written, and does not send the person to Config for it", () => {
    const h = html({ ...PUT, key: "" });
    expect(buttons(h)).toBe(0);
    expect(h).not.toContain("/config/rules");
  });

  it("says so for a trade manage does not ask", () => {
    expect(text(html(null))).toContain("no intent to give");
  });
});
