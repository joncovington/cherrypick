import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { IntentStrip } from "../src/pages/Live/IntentStrip";
import {
  IDLE,
  answerTrade,
  finished,
  restoreTo,
  starting,
  saveFailure,
  saveIntent,
} from "../src/pages/Live/stripAnswer";

/**
 * The strip on a position that asks what the trade is for (manage's `intent_ask`). The guards: it shows
 * only while manage asks, with manage's options, verdicts and suggestion and none of the console's own;
 * a put with no stable ID says where to answer instead of offering a write the route would refuse; a
 * save sends the trade key and the intent and nothing else; and Undo is offered only for a value the
 * route would write back.
 */

const PUT: ManageIntentAsk = {
  kind: "wheel",
  asked: true,
  current: null,
  options: ["wheel", "no_wheel"],
  verdicts: {
    wheel: { action: "HOLD", rule: null, reason: null },
    no_wheel: { action: "ROLL", rule: "roll.naked_put", reason: "tested" },
  },
  suggested: "wheel",
  evidence: ["2 completed wheel cycles on XYZ"],
  note: null,
  key: "trades.XYZ-261001-1.intent",
  scope: "trade",
};

const DIAGONAL: ManageIntentAsk = {
  kind: "pmcc",
  asked: true,
  current: null,
  options: ["pmcc", "diagonal"],
  verdicts: {},
  suggested: null,
  evidence: [],
  note: null,
  key: "trades.XYZ 280121C00040000.intent",
  scope: "contract",
};

const trade = (ask: ManageIntentAsk | null): ManageTrade =>
  ({ tradeId: "XYZ-261001-1", intentAsk: ask }) as unknown as ManageTrade;

function html(ask: ManageIntentAsk | null): string {
  return renderToString(
    <MemoryRouter>
      <IntentStrip t={trade(ask)} />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

const text = (h: string) =>
  h
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();

describe("the strip", () => {
  it("asks a put its question with manage's options, a verdict under each, and the suggestion", () => {
    const t = text(html(PUT));
    expect(t).toContain("Is this put a wheel?");
    expect(t).toContain("Wheel hold suggested");
    expect(t).toContain("Not a wheel roll");
    expect(t).toContain("2 completed wheel cycles on XYZ");
  });

  it("asks a diagonal its own question, with no verdicts", () => {
    const t = text(html(DIAGONAL));
    expect(t).toContain("Is this diagonal a PMCC?");
    expect(t).toContain("PMCC");
    expect(t).toContain("Just a diagonal");
  });

  it("shows nothing for a trade with no question or one already answered", () => {
    expect(html(null)).toBe("");
    expect(html({ ...PUT, asked: false, current: "wheel" })).toBe("");
  });

  it("offers no write for a put answered on its name, and points to Config", () => {
    const h = html({ ...PUT, scope: "name", key: "positions.XYZ.intent" });
    expect(h).not.toContain("<button");
    expect(text(h)).toContain("Its ID changes once its orders are known");
    expect(h).toContain('href="/config/rules"');
  });

  it("is a group beside the card, never a button inside it", () => {
    expect(html(PUT)).toMatch(/^<div class="int-strip" role="group"/);
  });
});

describe("what is written", () => {
  it("takes the trade key out of the ask's key, and none from a name's", () => {
    expect(answerTrade(PUT)).toBe("XYZ-261001-1");
    expect(answerTrade(DIAGONAL)).toBe("XYZ 280121C00040000");
    // The scope decides, whatever the key looks like: a name's answer is never written as a trade's.
    expect(answerTrade({ ...PUT, scope: "name" })).toBeNull();
    expect(
      answerTrade({ ...PUT, scope: "name", key: "positions.XYZ.intent" }),
    ).toBeNull();
    expect(answerTrade({ ...PUT, key: "positions.XYZ.intent" })).toBeNull();
  });

  it("offers answers only where there are options to give", () => {
    expect(html(PUT)).toContain("<button");
    expect(html({ ...PUT, options: [] })).toBe("");
  });

  it("sends the trade key and the intent and nothing else, and returns what it replaced", async () => {
    const sent: unknown[] = [];
    const r = await saveIntent(
      "XYZ-261001-1",
      "wheel",
      async (url, method, body) => {
        sent.push([url, method, body]);
        return { intent: "wheel", previous: null };
      },
    );
    expect(sent).toEqual([
      [
        "/api/config/intent",
        "POST",
        { trade: "XYZ-261001-1", intent: "wheel" },
      ],
    ]);
    expect(r).toEqual({ ok: true, intent: "wheel", previous: null });
  });

  it("says why a save failed, by what the route answered", async () => {
    const fail = (m: string) =>
      saveIntent("XYZ-261001-1", "wheel", async () =>
        Promise.reject(new Error(m)),
      );
    expect(await fail("/api/config/intent: HTTP 409")).toEqual({
      ok: false,
      why: saveFailure("HTTP 409"),
    });
    expect(saveFailure("HTTP 409")).toContain("changed on disk");
    expect(saveFailure("HTTP 404")).toContain("no readable config");
    expect(saveFailure("HTTP 403")).toContain("Reload");
    expect(saveFailure("")).toContain("Not saved");
  });

  it("puts back only what the route would write: nothing clears, an option returns, a label does not", () => {
    expect(restoreTo(null, PUT.options)).toBeNull();
    expect(restoreTo("no_wheel", PUT.options)).toBe("no_wheel");
    expect(restoreTo("income", PUT.options)).toBeUndefined();
  });
});

describe("the strip's state", () => {
  const saved = finished(starting(IDLE), {
    ok: true,
    intent: "wheel",
    previous: null,
  });

  it("holds the answer it saved, and a save in flight blocks another", () => {
    expect(saved).toEqual({
      saved: { intent: "wheel", previous: null },
      busy: false,
      why: null,
    });
    expect(starting(saved).busy).toBe(true);
  });

  it("keeps a saved answer, and Undo, when an Undo fails", () => {
    const failed = finished(starting(saved), { ok: false, why: "changed" });
    expect(failed.saved).toEqual({ intent: "wheel", previous: null });
    expect(failed).toMatchObject({ busy: false, why: "changed" });
  });

  it("returns to the open question once Undo clears the intent", () => {
    expect(
      finished(starting(saved), { ok: true, intent: null, previous: "wheel" }),
    ).toEqual(IDLE);
  });

  it("says why a first save failed and offers the answers again", () => {
    expect(finished(starting(IDLE), { ok: false, why: "no" })).toEqual({
      saved: null,
      busy: false,
      why: "no",
    });
  });

  it("clears the last failure when the next save starts", () => {
    expect(starting({ ...IDLE, why: "no" }).why).toBeNull();
  });
});

describe("a malformed ask", () => {
  it("shows nothing where the key cannot be written, but still points a name to Config", () => {
    expect(html({ ...PUT, key: "" })).toBe("");
    expect(html({ ...DIAGONAL, key: "trades..intent" })).toBe("");
    expect(
      html({ ...PUT, scope: "name", key: "positions.XYZ.intent" }),
    ).toContain("Config");
  });
});
