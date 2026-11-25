import { afterEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ConfigModelPayload, ManageAccount, ManageAttentionItem, ManageIntentCandidate, ManageWheel } from "@console/shared";
import { WheelCards } from "../src/pages/Live/WheelCards";
import { ExpiringCard, NearStrikeCard } from "../src/pages/Live/AttentionCard";
import { Choice, IntentChip, candidateFor } from "../src/pages/Live/IntentChoice";
import { IntentsCard } from "../src/pages/Config/IntentsCard";
import {
  POSITIONS,
  TRADES,
  candidateIntentOf,
  candidateTradeKey,
  intentEdits,
  intentOf,
  pendingIntents,
  pendingTradeIntents,
  stageCandidateIntent,
  stageIntent,
  stageTradeIntent,
  tradeIntentOf,
} from "../src/pages/Config/intents";
import { clearAllStaged, getStaged, sameValue } from "../src/pages/Config/stagedStore";

/**
 * Live → wheel's "is this a wheel?" (2026-11-12). The guards: a choice is only staged, as the whole
 * `positions` object with every other name's overrides kept; staging it back to the file's value
 * clears it; the chip sits on the needs-a-look row of the put it belongs to and nowhere else; the
 * puts no row shows get a card of their own; and a card or section with nothing to show is left out.
 */

afterEach(() => clearAllStaged());

function text(html: string): string {
  return html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
}

function model(positions: unknown, trades?: unknown): ConfigModelPayload {
  return {
    targets: {
      manage: { exists: true, doc: { positions, ...(trades === undefined ? {} : { trades }) }, mtime: 1, guarded: [], issues: [] },
    },
  } as unknown as ConfigModelPayload;
}

/** A model with no manage file at all. */
const noManageFile = { targets: { manage: { exists: false } } } as unknown as ConfigModelPayload;

function cand(over: Partial<ManageIntentCandidate> = {}): ManageIntentCandidate {
  return {
    tradeId: "XYZ-261001-1",
    underlying: "XYZ",
    legs: ["-1 2026-11-20 50P"],
    contracts: 1,
    strikes: [50],
    expiration: "2026-11-20",
    dte: 38,
    cashSecured: 5000,
    netIfAssigned: 49,
    earnings: null,
    history: { cyclesComplete: 2, assignments: 0 },
    wheelPath: true,
    now: { action: "CLOSE", rule: "credit.breached", reason: "short strike in the money" },
    asWheel: { action: "ROLL_OR_CLOSE", rule: "wheel.tested", reason: "roll, close or take the assignment" },
    changes: true,
    suggested: null,
    evidence: [],
    note: null,
    key: "trades.XYZ-261001-1.intent",
    ...over,
  };
}

function item(over: Partial<ManageAttentionItem> = {}): ManageAttentionItem {
  return {
    underlying: "XYZ",
    symbol: "XYZ   261120P00050000",
    leg: "2026-11-20 50P",
    right: "P",
    strike: 50,
    expiration: "2026-11-20",
    dte: 38,
    quantity: -1,
    spot: 49.5,
    distance: -0.01,
    mid: 1.6,
    extrinsic: 1.1,
    level: "in the money",
    why: "in the money",
    ...over,
  };
}

function wheel(over: Partial<ManageWheel> = {}): ManageWheel {
  return { cycles: [], flagged: [], outside: {}, leftOut: [], names: null, bandsDay: null, bandsMissing: null, intentCandidates: [], error: null, ...over };
}

function account(w: ManageWheel, items: ManageAttentionItem[] = []): ManageAccount {
  return {
    account: "****1234",
    netLiq: 100000,
    maintenance: null,
    buyingPower: null,
    riskBudget: null,
    pnlGross: null,
    counts: {},
    trades: [],
    excluded: [],
    portfolio: null,
    wheel: w,
    pmcc: null,
    attention: { nearPct: 0.03, expiringDays: 7, items, expiring: [] },
  } as ManageAccount;
}

function page(a: ManageAccount): string {
  const qc = new QueryClient();
  return text(
    renderToString(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <WheelCards a={a} />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

describe("staging an intent", () => {
  it("stages the whole positions object, every other name's overrides kept", () => {
    const m = model({ AAA: { disable: ["debit.stop"], note: "hedge" } });
    expect(stageIntent(m, "XYZ", "wheel")).toBeNull();
    const staged = getStaged("manage", POSITIONS);
    expect(staged.has).toBe(true);
    expect(staged.value).toEqual({ AAA: { disable: ["debit.stop"], note: "hedge" }, XYZ: { intent: "wheel" } });
    expect(intentOf(m, "XYZ")).toEqual({ intent: "wheel", unsaved: true });
    expect(pendingIntents(m)).toEqual([{ symbol: "XYZ", from: null, to: "wheel" }]);
  });

  it("staging back to the file's value clears the change", () => {
    const m = model({ XYZ: { intent: "wheel" } });
    stageIntent(m, "XYZ", "no_wheel");
    expect(getStaged("manage", POSITIONS).has).toBe(true);
    stageIntent(m, "XYZ", "wheel");
    expect(getStaged("manage", POSITIONS).has).toBe(false);
  });

  it("clearing a name's only override removes its entry", () => {
    const m = model({ XYZ: { intent: "wheel" } });
    stageIntent(m, "XYZ", null);
    expect(getStaged("manage", POSITIONS).value).toEqual({});
  });

  it("says why when manage.json has no positions block, and stages nothing", () => {
    expect(stageIntent(model(undefined), "XYZ", "wheel")).toContain("no positions block");
    expect(getStaged("manage", POSITIONS).has).toBe(false);
  });

  it("compares the override objects by value, not by key order", () => {
    expect(sameValue({ a: { intent: "wheel", note: "x" } }, { a: { note: "x", intent: "wheel" } })).toBe(true);
    expect(sameValue({ a: { intent: "wheel" } }, { a: { intent: "pmcc" } })).toBe(false);
  });
});

describe("staging a trade's intent", () => {
  const ID = "XYZ-261001-1";

  it("stages the whole trades object, every other entry kept, and the name's positions untouched", () => {
    const m = model({ XYZ: { intent: "pmcc" } }, { "XYZ 270319C00013000": { intent: "pmcc" }, "AAA-261001-1": { note: "hand" } });
    expect(stageTradeIntent(m, ID, "wheel")).toBeNull();
    expect(getStaged("manage", TRADES).value).toEqual({
      "XYZ 270319C00013000": { intent: "pmcc" },
      "AAA-261001-1": { note: "hand" },
      [ID]: { intent: "wheel" },
    });
    expect(getStaged("manage", POSITIONS).has).toBe(false);
    expect(tradeIntentOf(m, ID)).toEqual({ intent: "wheel", unsaved: true });
    expect(pendingTradeIntents(m)).toEqual([{ key: ID, from: null, to: "wheel" }]);
  });

  it("stages into a file with no trades block (an older copy): a save adds the block", () => {
    const m = model({});
    expect(stageTradeIntent(m, ID, "wheel")).toBeNull();
    expect(getStaged("manage", TRADES).value).toEqual({ [ID]: { intent: "wheel" } });
  });

  it("staging back to the file's value clears the change, an absent block included", () => {
    const saved = model({}, { [ID]: { intent: "wheel" } });
    stageTradeIntent(saved, ID, "no_wheel");
    expect(getStaged("manage", TRADES).has).toBe(true);
    stageTradeIntent(saved, ID, "wheel");
    expect(getStaged("manage", TRADES).has).toBe(false);

    const absent = model({});
    stageTradeIntent(absent, ID, "wheel");
    stageTradeIntent(absent, ID, null);
    expect(getStaged("manage", TRADES).has).toBe(false);
  });

  it("clearing a trade's only override removes its entry, and keeps a note", () => {
    stageTradeIntent(model({}, { [ID]: { intent: "wheel" } }), ID, null);
    expect(getStaged("manage", TRADES).value).toEqual({});
    clearAllStaged();
    stageTradeIntent(model({}, { [ID]: { intent: "wheel", note: "keep" } }), ID, null);
    expect(getStaged("manage", TRADES).value).toEqual({ [ID]: { note: "keep" } });
  });

  it("says why when there is no manage file, and stages nothing", () => {
    expect(stageTradeIntent(noManageFile, ID, "wheel")).toContain("no manage.json");
    expect(getStaged("manage", TRADES).has).toBe(false);
  });
});

describe("where a candidate's choice is written", () => {
  it("is its own trade's entry when its ID is stable, the name's while it is provisional", () => {
    expect(candidateTradeKey(cand())).toBe("XYZ-261001-1");
    expect(candidateTradeKey(cand({ key: "positions.XYZ.intent", tradeId: "XYZ-open-ceda13" }))).toBeNull();
    expect(candidateTradeKey(cand({ tradeId: null }))).toBeNull();
  });

  it("stages a stable trade into trades and a provisional one into positions", () => {
    const m = model({}, {});
    expect(stageCandidateIntent(m, cand(), "wheel")).toBeNull();
    expect(getStaged("manage", TRADES).value).toEqual({ "XYZ-261001-1": { intent: "wheel" } });
    expect(getStaged("manage", POSITIONS).has).toBe(false);
    clearAllStaged();
    expect(stageCandidateIntent(m, cand({ key: "positions.XYZ.intent", tradeId: "XYZ-open-ceda13" }), "wheel")).toBeNull();
    expect(getStaged("manage", POSITIONS).value).toEqual({ XYZ: { intent: "wheel" } });
    expect(getStaged("manage", TRADES).has).toBe(false);
  });

  it("is not answered by a name's pmcc, which is for another trade on the name", () => {
    const m = model({ XYZ: { intent: "pmcc" } });
    expect(candidateIntentOf(m, cand())).toEqual({ intent: null, unsaved: false });
    // A provisional trade is keyed to its name, so the name's intent is its answer.
    expect(candidateIntentOf(m, cand({ key: "positions.XYZ.intent", tradeId: "XYZ-open-ceda13" })).intent).toBe("pmcc");
  });

  it("reads a trade's own saved or staged intent as its answer", () => {
    const m = model({ XYZ: { intent: "pmcc" } }, { "XYZ-261001-1": { intent: "wheel" } });
    expect(candidateIntentOf(m, cand())).toEqual({ intent: "wheel", unsaved: false });
  });
});

describe("what a save writes", () => {
  it("is each staged block whole, and nothing that was not staged", () => {
    const m = model({ XYZ: { intent: "pmcc" } }, {});
    expect(intentEdits(m)).toEqual([]);
    stageTradeIntent(m, "XYZ-261001-1", "wheel");
    expect(intentEdits(m)).toEqual([{ pointer: TRADES, value: { "XYZ-261001-1": { intent: "wheel" } } }]);
    stageIntent(m, "AAA", "wheel");
    expect(intentEdits(m).map((e) => e.pointer).sort()).toEqual([POSITIONS, TRADES]);
  });
});

describe("the choice panel", () => {
  const html = (c: ManageIntentCandidate) =>
    text(renderToString(<Choice c={c} model={model({}, {})} />));

  it("says why a wheel is suggested, in one line, and never applies it", () => {
    const out = html(cand({ suggested: "wheel", evidence: ["2 completed wheel cycles on XYZ", "an open wheel cycle on XYZ reconciles"] }));
    expect(out).toContain("Looks like a wheel: 2 completed wheel cycles on XYZ; an open wheel cycle on XYZ reconciles.");
    expect(getStaged("manage", TRADES).has).toBe(false);
  });

  it("says when the name's intent is for another trade, apart from any suggestion", () => {
    const note = "the name's intent 'pmcc' is for another trade on XYZ, not this put";
    const out = html(cand({ note }));
    expect(out).toContain("The name's intent 'pmcc' is for another trade on XYZ, not this put.");
    expect(out).not.toContain("Looks like a wheel");
    const both = html(cand({ note, suggested: "wheel", evidence: ["2 completed wheel cycles on XYZ"] }));
    expect(both).toContain("Looks like a wheel: 2 completed wheel cycles on XYZ.");
    expect(both).toContain("for another trade");
  });

  it("says nothing extra when manage has nothing to say", () => {
    const none = html(cand());
    expect(none).not.toContain("Looks like a wheel");
    expect(none).not.toContain("for another trade");
  });
});

describe("Config › Intents", () => {
  const card = (m: ConfigModelPayload) =>
    text(
      renderToString(
        <QueryClientProvider client={new QueryClient()}>
          <IntentsCard model={m} />
        </QueryClientProvider>,
      ),
    );

  it("lists a trade's own intent beside the names', and a pmcc on a long's contract", () => {
    const out = card(model({ XYZ: { intent: "pmcc" } }, { "XYZ-261001-1": { intent: "wheel" }, "XYZ 270319C00013000": { intent: "pmcc" } }));
    expect(out).toContain("XYZ-261001-1");
    expect(out).toContain("XYZ 270319C00013000");
  });

  it("words a call contract as a PMCC's long and any other key as a trade, as manage keys them", () => {
    const out = card(model({}, { "XYZ 270319C00013000": { intent: "pmcc" }, "XYZ 270319P00013000": { intent: "wheel" } }));
    expect(out.match(/this PMCC's long only/g)).toHaveLength(1);
    expect(out.match(/this trade only/g)).toHaveLength(1);
  });

  it("still offers trade intents when the file has no positions block", () => {
    const m = { targets: { manage: { exists: true, doc: { trades: { "XYZ-261001-1": { intent: "wheel" } } }, mtime: 1, guarded: [], issues: [] } } } as unknown as ConfigModelPayload;
    expect(card(m)).toContain("XYZ-261001-1");
  });

  it("names the key a staged trade intent will be written at", () => {
    const m = model({}, {});
    stageTradeIntent(m, "XYZ-261001-1", "wheel");
    expect(card(m)).toContain("trades.XYZ-261001-1.intent");
  });
});

describe("which row a candidate belongs to", () => {
  it("is the short put of the same name, expiry and strike", () => {
    expect(candidateFor([cand()], item())).not.toBeNull();
    expect(candidateFor([cand()], item({ strike: 45 }))).toBeNull();
    expect(candidateFor([cand()], item({ right: "C" }))).toBeNull();
    expect(candidateFor([cand()], item({ quantity: 1 }))).toBeNull();
    expect(candidateFor([cand()], item({ underlying: "AAA" }))).toBeNull();
  });
});

describe("the wheel tab's needs a look", () => {
  it("puts the chip on the put's own row, and only there", () => {
    // The card as the tab draws it, the tab's own `extra`; a call on the same name gets nothing.
    const cands = [cand()];
    const html = renderToString(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <NearStrikeCard
            a={{ nearPct: 0.03, expiringDays: 7, items: [item(), item({ symbol: "XYZ   261120C00055000", leg: "2026-11-20 55C", right: "C", strike: 55 })], expiring: [] }}
            hideEmpty
            extra={(leg) => {
              const c = candidateFor(cands, leg);
              return c === null ? null : <IntentChip c={c} />;
            }}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(text(html).match(/no intent/g)).toHaveLength(1);
  });

  it("a put a row shows gets no card of its own", () => {
    // No running cycle holds it here, so no row shows it and the card does: the two never both.
    const out = page(account(wheel({ intentCandidates: [cand()] }), [item()]));
    expect(out.match(/is it a wheel\?/g)).toHaveLength(1);
  });

  it("lists a put no row shows in its own card", () => {
    const out = page(account(wheel({ intentCandidates: [cand({ underlying: "FFF", legs: ["-1 2026-12-18 30P"], strikes: [30] })] })));
    expect(out).toContain("no intent yet");
    expect(out).toContain("is it a wheel?");
    expect(out).toContain("$5,000 cash secured");
    // Nothing near and nothing expiring: those two cards are left out.
    expect(out).not.toContain("shorts near their strike");
    expect(out).not.toContain("expiring in");
  });

  it("leaves the whole section out when nothing in it has anything to show", () => {
    expect(page(account(wheel()))).not.toContain("What needs a look?");
  });
});

describe("a needs-a-look card with nothing to show", () => {
  const empty = { nearPct: 0.03, expiringDays: 7, items: [], expiring: [] };
  it("is left out where the tab asks for it, and still says all clear elsewhere", () => {
    expect(renderToString(<NearStrikeCard a={empty} hideEmpty />)).toBe("");
    expect(renderToString(<ExpiringCard a={empty} hideEmpty />)).toBe("");
    expect(text(renderToString(<NearStrikeCard a={empty} />))).toContain("nothing");
  });
});
