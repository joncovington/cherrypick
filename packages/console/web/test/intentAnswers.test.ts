import { describe, expect, it } from "vitest";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import {
  answer,
  pruneIntentAnswers,
  savedFor,
} from "../src/pages/Live/intentAnswers";

/**
 * What the console has saved for a trade's intent, shared by the card's strip and the opened trade's tab.
 * The guards: an answer given in one place reads the same in the other; it is read only under the ask it
 * was given under; once manage has re-read the ask it is dropped and does not come back if the question
 * reopens; and a failed save leaves what was saved in place.
 */

const ask = (over: Partial<ManageIntentAsk> = {}): ManageIntentAsk => ({
  kind: "wheel",
  asked: true,
  current: null,
  options: ["wheel", "no_wheel"],
  verdicts: {},
  suggested: null,
  evidence: [],
  note: null,
  key: "trades.XYZ-261001-1.intent",
  scope: "trade",
  ...over,
});

const ok =
  (previous: string | null) =>
  async (_u: string, _m: "POST", body: unknown) => ({
    intent: (body as { intent: string | null }).intent,
    previous,
  });
const boom = async () => {
  throw new Error("/api/config/intent: HTTP 409");
};
const trade = (a: ManageIntentAsk): ManageTrade =>
  ({ tradeId: "XYZ-261001-1", intentAsk: a }) as unknown as ManageTrade;

describe("the saved answers", () => {
  it("reads the same to every place that asks", async () => {
    const t = "SHARE-261001-1";
    await answer(t, ask(), "wheel", ok(null));
    // The strip and the tab each read with the ask they were given: the same one.
    expect(savedFor(t, ask()).saved).toEqual({
      intent: "wheel",
      previous: null,
    });
    expect(savedFor(t, ask({ suggested: "wheel" })).saved).toEqual({
      intent: "wheel",
      previous: null,
    });
  });

  it("is read only under the ask it was given under", async () => {
    const t = "STAMP-261001-1";
    await answer(t, ask(), "wheel", ok(null));
    expect(
      savedFor(t, ask({ asked: false, current: "wheel" })).saved,
    ).toBeNull();
    expect(savedFor(t, ask({ current: "no_wheel" })).saved).toBeNull();
  });

  it("is dropped once manage has re-read it, and does not come back if the question reopens", async () => {
    const t = "REOPEN-261001-1";
    const key = `trades.${t}.intent`;
    await answer(t, ask({ key }), "wheel", ok(null));
    // manage re-evaluates and reads the answer
    pruneIntentAnswers([trade(ask({ key, asked: false, current: "wheel" }))]);
    // the intent is later cleared in Config and the question reopens
    expect(savedFor(t, ask({ key })).saved).toBeNull();
  });

  it("is kept while the ask is unchanged, and for a trade the evaluation does not list", async () => {
    const t = "KEEP-261001-1";
    const key = `trades.${t}.intent`;
    await answer(t, ask({ key }), "wheel", ok(null));
    pruneIntentAnswers([trade(ask({ key }))]);
    pruneIntentAnswers([]);
    expect(savedFor(t, ask({ key })).saved).toEqual({
      intent: "wheel",
      previous: null,
    });
  });

  it("is busy while a save is in flight, and not after", async () => {
    const t = "BUSY-261001-1";
    let during: boolean | null = null;
    await answer(t, ask(), "wheel", async (_u, _m, body) => {
      during = savedFor(t, ask()).busy;
      return {
        intent: (body as { intent: string | null }).intent,
        previous: null,
      };
    });
    expect(during).toBe(true);
    expect(savedFor(t, ask()).busy).toBe(false);
  });

  it("keeps what was saved when a later save fails, with the reason", async () => {
    const t = "FAIL-261001-1";
    await answer(t, ask(), "wheel", ok(null));
    await answer(t, ask(), null, boom);
    const s = savedFor(t, ask());
    expect(s.saved).toEqual({ intent: "wheel", previous: null });
    expect(s.busy).toBe(false);
    expect(s.why).toContain("changed on disk");
  });

  it("goes back to the open question when Undo clears the intent", async () => {
    const t = "UNDO-261001-1";
    await answer(t, ask(), "wheel", ok(null));
    await answer(t, ask(), null, ok("wheel"));
    expect(savedFor(t, ask()).saved).toBeNull();
  });
});
