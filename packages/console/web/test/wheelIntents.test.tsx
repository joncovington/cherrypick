import { afterEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ConfigModelPayload, ManageAccount, ManageAttentionItem, ManageIntentCandidate, ManageWheel } from "@console/shared";
import { WheelCards } from "../src/pages/Live/WheelCards";
import { ExpiringCard, NearStrikeCard } from "../src/pages/Live/AttentionCard";
import { IntentChip, candidateFor } from "../src/pages/Live/IntentChoice";
import { POSITIONS, intentOf, pendingIntents, stageIntent } from "../src/pages/Config/intents";
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

function model(positions: unknown): ConfigModelPayload {
  return {
    targets: {
      manage: { exists: true, doc: { positions }, mtime: 1, guarded: [], issues: [] },
    },
  } as unknown as ConfigModelPayload;
}

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
    key: "positions.XYZ.intent",
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
