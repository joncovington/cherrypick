import { afterEach, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageCandidate, ManageCandidates } from "@console/shared";
import { ListViewContext, ViewSwitch } from "../src/components/ViewSwitch";
import { AddingTradesCard } from "../src/pages/Live/IncomeCard";
import { TradeCards, candidateModel } from "../src/pages/Live/TradeCard";
import { writePref } from "../src/lib/prefs";

/**
 * Cards or rows (2026-11-06): a list draws cards unless the card around it chose rows; a row is drawn
 * from the same model as its card, and the choice is remembered per card.
 */

function candidate(over: Partial<ManageCandidate>): ManageCandidate {
  return {
    underlying: "AAA",
    kind: "put_spread",
    alternativeTo: null,
    expiry: "2026-12-18",
    dte: 42,
    legs: [
      { strike: 50, right: "P", quantity: -1, bid: 1, ask: 1.1, delta: -0.2 },
      { strike: 45, right: "P", quantity: 1, bid: 0.4, ask: 0.5, delta: -0.1 },
    ],
    units: 1,
    binding: "per-trade risk",
    perUnit: { credit: 0.6, natural: 0.5, cash: 60, maxLoss: 440, buyingPower: 440, stressLoss: 300, theta: 1, bwd: 1 },
    thetaPerBp: null,
    earningsDate: null,
    earningsUnknown: false,
    alternative: null,
    ifRoom: null,
    leveraged: null,
    heldSameExpiry: false,
    odds: null,
    ...over,
  };
}

const models = [candidate({}), candidate({ underlying: "BBB" })].map((c) => candidateModel(c, 352, null, { price: "0.60 cr", narrowed: null }));

describe("cards or rows", () => {
  afterEach(() => {
    void writePref("view.adding-trades", undefined);
    void writePref("minPremium.live-income", undefined);
  });

  it("draws cards outside a card that chose rows, and one row a trade inside one, from the same figures", () => {
    const cards = renderToString(<TradeCards models={models} />);
    expect(cards.match(/tc-card/g)?.length).toBe(2);
    expect(cards).not.toContain("tr-row");
    const rows = renderToString(
      <ListViewContext.Provider value="rows">
        <TradeCards models={models} />
      </ListViewContext.Provider>,
    );
    expect(rows.match(/class="tr-row"/g)?.length).toBe(2);
    // A row opens its card in place; closed, none is drawn.
    expect(rows).not.toContain("tc-card");
    expect(rows).toContain('aria-expanded="false"');
    // The row's cash and cap words are the card's own.
    expect(rows).toContain("+$60.00");
    expect(rows).toContain("max loss $300.00 of a $352.00 cap");
  });

  it("marks the chosen view in the switch", () => {
    const html = renderToString(<ViewSwitch view="rows" onChange={() => undefined} />);
    expect(html).toMatch(/aria-pressed="true" aria-label="Rows"/);
    expect(html).toMatch(/aria-pressed="false" aria-label="Cards"/);
  });

  it("remembers the adding-trades card's choice", () => {
    void writePref("minPremium.live-income", 0);
    const c: ManageCandidates = {
      sourceSession: "2026-11-06",
      takenAt: null,
      accountBasis: "cash",
      names: 10,
      excluded: {},
      fits: [candidate({})],
      fitsTotal: 1,
      tooBig: [],
      tooBigTotal: 0,
      waiting: [],
      waitingTotal: 0,
      bpUsedPct: 0.3,
      bpCapPct: 0.35,
      bpWarnOver: 0.1,
      error: null,
      caps: { perTradeRiskPct: 0.03, notionalPerNamePct: 0.25, maxNameThetaShare: 0.25 },
      exEarnings: null,
    };
    expect(renderToString(<AddingTradesCard c={c} t={null} netLiq={10000} />)).toContain("tc-card");
    void writePref("view.adding-trades", "rows");
    const rows = renderToString(<AddingTradesCard c={c} t={null} netLiq={10000} />);
    expect(rows).toContain('class="tr-row"');
    expect(rows).not.toContain("tc-card");
  });
});
