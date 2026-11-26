import { afterEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ConfigModelPayload, ManageAccount, ManageAttentionItem, ManageIntentCandidate, ManageWheel } from "@console/shared";
import { WheelCards } from "../src/pages/Live/WheelCards";
import { ExpiringCard, NearStrikeCard } from "../src/pages/Live/AttentionCard";
import { IntentChip, NoIntentCard, candidateFor, candidateKey } from "../src/pages/Live/IntentChoice";
import { IntentsCard } from "../src/pages/Config/IntentsCard";
import { POSITIONS, intentEdits, intentOf, pendingIntents, stageIntent, tradeIntents } from "../src/pages/Config/intents";
import { clearAllStaged, getStaged, sameValue } from "../src/pages/Config/stagedStore";

/**
 * Live → wheel's "no intent yet" and Config › Intents. The question is asked on the position (see
 * intentStrip.test.tsx and intentTab.test.tsx); these are the surfaces that point to it. The guards: a
 * name's intent is staged as the whole `positions` object with every other name's overrides kept, and
 * staging it back to the file's value clears it; a trade's intent is listed in Config but never staged
 * there; the chip sits on the needs-a-look row of the put it belongs to and nowhere else, and links to
 * where the put is answered; the puts no row shows get a card of their own; and a card or section with
 * nothing to show is left out.
 */

afterEach(() => clearAllStaged());

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function model(positions: unknown, trades?: unknown): ConfigModelPayload {
  return {
    targets: {
      manage: {
        exists: true,
        doc: { positions, ...(trades === undefined ? {} : { trades }) },
        mtime: 1,
        guarded: [],
        issues: [],
      },
    },
  } as unknown as ConfigModelPayload;
}

/** A model with no manage file at all. */
const noManageFile = {
  targets: { manage: { exists: false } },
} as unknown as ConfigModelPayload;

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
    now: {
      action: "CLOSE",
      rule: "credit.breached",
      reason: "short strike in the money",
    },
    asWheel: {
      action: "ROLL_OR_CLOSE",
      rule: "wheel.tested",
      reason: "roll, close or take the assignment",
    },
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
  return {
    cycles: [],
    flagged: [],
    outside: {},
    leftOut: [],
    names: null,
    bandsDay: null,
    bandsMissing: null,
    intentCandidates: [],
    error: null,
    ...over,
  };
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
    expect(staged.value).toEqual({
      AAA: { disable: ["debit.stop"], note: "hedge" },
      XYZ: { intent: "wheel" },
    });
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

describe("what a save writes", () => {
  it("is the staged positions whole, and nothing that was not staged", () => {
    const m = model({ XYZ: { intent: "pmcc" } }, {});
    expect(intentEdits(m)).toEqual([]);
    stageIntent(m, "AAA", "wheel");
    expect(intentEdits(m)).toEqual([
      {
        pointer: POSITIONS,
        value: { XYZ: { intent: "pmcc" }, AAA: { intent: "wheel" } },
      },
    ]);
  });
});

describe("Config › Intents", () => {
  const raw = (m: ConfigModelPayload) =>
    renderToString(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <IntentsCard model={m} />
        </MemoryRouter>
      </QueryClientProvider>,
    ).replace(/<!-- -->/g, "");
  const card = (m: ConfigModelPayload) => text(raw(m));

  it("lists a trade's own intent beside the names', and a pmcc on a long's contract", () => {
    const out = card(
      model(
        { XYZ: { intent: "pmcc" } },
        {
          "XYZ-261001-1": { intent: "wheel" },
          "XYZ 270319C00013000": { intent: "pmcc" },
        },
      ),
    );
    expect(out).toContain("XYZ-261001-1");
    expect(out).toContain("XYZ 270319C00013000");
  });

  it("words a call contract as a PMCC's long and any other key as a trade, as manage keys them", () => {
    const out = card(
      model(
        {},
        {
          "XYZ 270319C00013000": { intent: "pmcc" },
          "XYZ 270319P00013000": { intent: "wheel" },
        },
      ),
    );
    expect(out.match(/this PMCC's long only/g)).toHaveLength(1);
    expect(out.match(/this trade only/g)).toHaveLength(1);
  });

  it("lists trades read only: no select to change one, and a link to where it is answered", () => {
    const h = raw(
      model(
        {},
        {
          "XYZ-261001-1": { intent: "wheel" },
          "XYZ 270319C00013000": { intent: "pmcc" },
        },
      ),
    );
    expect(h).not.toContain("<select");
    expect(h).toContain('href="/live/positions?trade=XYZ-261001-1&amp;tab=intent"');
    expect(h).toContain('href="/live/pmcc"');
  });

  it("can clear a trade's entry, one button a trade, so one a closed trade left behind is removed", () => {
    const h = raw(model({}, { "XYZ-261001-1": { intent: "wheel" }, "XYZ 270319C00013000": { intent: "pmcc" } }));
    expect(h.match(/>clear</g)).toHaveLength(2);
    expect(raw(model({ XYZ: { intent: "wheel" } }, {}))).not.toContain(">clear<");
  });

  it("still lets a name's intent be changed, with a select", () => {
    expect(raw(model({ XYZ: { intent: "wheel" } }, { "AAA-261001-1": { intent: "wheel" } }))).toContain("<select");
  });

  it("lists trades even when the file has no positions block", () => {
    const m = {
      targets: {
        manage: {
          exists: true,
          doc: { trades: { "XYZ-261001-1": { intent: "wheel" } } },
          mtime: 1,
          guarded: [],
          issues: [],
        },
      },
    } as unknown as ConfigModelPayload;
    expect(card(m)).toContain("XYZ-261001-1");
  });

  it("reads a trade's intent only from a trade that holds one, skipping notes and entries without", () => {
    const m = model(
      {},
      {
        _comment: { intent: "x" },
        "A-261001-1": { note: "no intent" },
        "B-261001-1": { intent: "no_wheel" },
      },
    );
    expect(tradeIntents(m)).toEqual([{ key: "B-261001-1", intent: "no_wheel" }]);
    expect(tradeIntents(noManageFile)).toEqual([]);
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
      <MemoryRouter>
        <NearStrikeCard
          a={{
            nearPct: 0.03,
            expiringDays: 7,
            items: [
              item(),
              item({
                symbol: "XYZ   261120C00055000",
                leg: "2026-11-20 55C",
                right: "C",
                strike: 55,
              }),
            ],
            expiring: [],
          }}
          hideEmpty
          extra={(leg) => {
            const c = candidateFor(cands, leg);
            return c === null ? null : <IntentChip c={c} />;
          }}
        />
      </MemoryRouter>,
    );
    expect(text(html).match(/no intent/g)).toHaveLength(1);
  });

  it("links the chip to the trade's Intent tab, or to Config while its ID is provisional", () => {
    const chip = (c: ManageIntentCandidate) =>
      renderToString(
        <MemoryRouter>
          <IntentChip c={c} />
        </MemoryRouter>,
      ).replace(/<!-- -->/g, "");
    expect(chip(cand())).toContain('href="/live/positions?trade=XYZ-261001-1&amp;tab=intent"');
    const provisional = chip(cand({ tradeId: null, key: "positions.XYZ.intent" }));
    expect(provisional).toContain('href="/config/rules"');
    expect(provisional).not.toContain("/live/positions");
    // An ID with the answer keyed to the name (the name's intent is for another trade) is answered on the name.
    expect(chip(cand({ key: "positions.XYZ.intent" }))).toContain('href="/config/rules"');
  });

  it("carries the account into the link, so a trade is found on the account that holds it", () => {
    const chip = renderToString(
      <MemoryRouter>
        <IntentChip c={cand()} account="****2375" />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");
    expect(chip).toContain("&amp;acct=****2375");
    expect(chip).not.toContain("acct=null");
    const none = renderToString(
      <MemoryRouter>
        <IntentChip c={cand()} />
      </MemoryRouter>,
    );
    expect(none).not.toContain("acct=");
  });

  it("waits for a stable ID, with no link, while the name's intent is for another trade", () => {
    const held = cand({
      tradeId: null,
      key: "positions.XYZ.intent",
      note: "the name's intent 'pmcc' is for another trade on XYZ, not this put",
    });
    const chip = renderToString(
      <MemoryRouter>
        <IntentChip c={held} />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");
    expect(chip).not.toContain("<a ");
    expect(text(chip)).toContain("waits for an ID");
    const card = text(
      renderToString(
        <MemoryRouter>
          <NoIntentCard cands={[held]} shown={new Set()} />
        </MemoryRouter>,
      ),
    );
    expect(card).toContain("waits for a stable trade ID");
    expect(card).not.toContain("answer on Config");
  });

  it("lists only the puts no needs-a-look row shows", () => {
    const shown = cand();
    const other = cand({ tradeId: "FFF-261001-1", underlying: "FFF", legs: ["-1 2026-12-18 30P"], strikes: [30], key: "trades.FFF-261001-1.intent" });
    const out = text(
      renderToString(
        <MemoryRouter>
          <NoIntentCard cands={[shown, other]} shown={new Set([candidateKey(shown)])} />
        </MemoryRouter>,
      ),
    );
    expect(out).toContain("FFF");
    expect(out).not.toContain("XYZ");
    expect(
      renderToString(
        <MemoryRouter>
          <NoIntentCard cands={[shown]} shown={new Set([candidateKey(shown)])} />
        </MemoryRouter>,
      ),
    ).toBe("");
  });

  it("stages and saves nothing: a wheel tab with candidates leaves the staged edits empty", () => {
    page(account(wheel({ intentCandidates: [cand()] }), [item()]));
    expect(getStaged("manage", POSITIONS).has).toBe(false);
  });

  it("a put a row shows gets no card of its own", () => {
    // No running cycle holds it here, so no row shows it and the card does: the two never both.
    const out = page(account(wheel({ intentCandidates: [cand()] }), [item()]));
    expect(out.match(/answer on positions/g)).toHaveLength(1);
    expect(out).not.toContain("no intent ›");
  });

  it("lists a put no row shows in its own card, with a link to answer it", () => {
    const html = renderToString(
      <MemoryRouter>
        <WheelCards
          a={account(
            wheel({
              intentCandidates: [
                cand({
                  underlying: "FFF",
                  legs: ["-1 2026-12-18 30P"],
                  strikes: [30],
                }),
              ],
            }),
          )}
        />
      </MemoryRouter>,
    ).replace(/<!-- -->/g, "");
    const out = text(html);
    expect(out).toContain("no intent yet");
    expect(out).toContain("answer on positions");
    expect(out).toContain("$5,000 cash secured");
    expect(html).toContain('href="/live/positions?trade=XYZ-261001-1&amp;tab=intent&amp;acct=****1234"');
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
