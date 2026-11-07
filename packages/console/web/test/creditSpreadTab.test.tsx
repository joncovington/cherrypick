import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { CreditSpreadPayload, CreditSpreadPrice, CreditSpreadRow } from "@console/shared";
import { writePref } from "../src/lib/prefs";

/**
 * Our credit-spread list on the page: what the defaults hide, the order, the untested note and the
 * width table. The page decides nothing -- every figure is the builder's -- so the tests are about
 * the cuts and the wording.
 */

function price(over: Partial<CreditSpreadPrice> = {}): CreditSpreadPrice {
  return {
    long: 94,
    longDelta: -0.3,
    width: 2,
    credit: 0.74,
    creditMid: 0.78,
    creditWidth: 0.37,
    maxLoss: 1.26,
    creditMaxLoss: 0.5873,
    passes: true,
    ...over,
  };
}

function row(over: Partial<CreditSpreadRow>): CreditSpreadRow {
  return {
    symbol: "AAA",
    type: "Stock",
    spread: "put",
    setups: ["pullback"],
    signals: [{ setup: "pullback", date: "2026-10-21", ago: 2 }],
    conflict: false,
    spot: 100,
    expiry: "2026-11-20",
    dte: 31,
    window: "30-45",
    move: 5.13,
    short: { strike: 96, delta: -0.398, iv: 0.31, bid: 1.9, ask: 1.98 },
    pick: price(),
    widths: [
      { unit: "strikes", size: 1, price: price({ long: 95, width: 1, credit: 0.45, creditWidth: 0.45 }) },
      { unit: "strikes", size: 5, price: null },
      { unit: "move", size: 0.5, price: price() },
    ],
    earnings: "clear",
    earningsDate: "2027-01-28",
    earningsEstimated: false,
    exDividend: null,
    exDividendDate: null,
    leveraged: null,
    ...over,
  };
}

function base(rows: CreditSpreadRow[]): CreditSpreadPayload {
  return {
    day: "2026-10-23",
    days: ["2026-10-23"],
    builtAt: "b",
    chainTakenAt: "2026-10-23T18:45:00Z",
    chainComplete: true,
    chainProblem: null,
    names: 488,
    dropped: { "no fresh setup entry": 370 },
    built: true,
    rule: { shortDelta: 0.4, creditShare: [0.35, 0.4], recentSessions: 5, tested: "not yet: item 17" },
    rows,
  };
}

let payload: CreditSpreadPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useCreditSpreadList: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { CreditSpreadTab, SetupBody, WidthsBody, CREDIT_SORTS, creditHelp, applyFilters } = await import("../src/pages/Screeners/CreditSpreadTab");

const text = (html: string) => html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'");

/** A setup's context and its widths, as the lightbox draws them (it opens on a click, so render the bodies). */
function setupBody(r: CreditSpreadRow): string {
  return text(renderToString(<SetupBody r={r} />));
}
function widthsBody(r: CreditSpreadRow): string {
  return text(renderToString(<WidthsBody r={r} />));
}

function render(url = "/screeners/credit-spreads"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <CreditSpreadTab />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

/** The names on the page, in order: each card's aria-label starts with its symbol. */
const symbols = (html: string) => [...html.matchAll(/aria-label="([A-Z0-9]+): (?:bull put|bear call)/g)].map((m) => m[1]);

function help(): string {
  return text(renderToString(<>{creditHelp("not yet: item 17", undefined, "35–40%", [["no fresh setup entry", 370]], 5, 1, 2).body}</>)).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
}

describe("our credit-spread list", () => {
  it("always flags that no edge was found, with rows and without, the text behind the ?", () => {
    payload = base([row({})]);
    const html = render();
    expect(html).toContain('<button type="button" class="help-flag" title="Open the ? for the full text">no tested edge</button>');
    expect(html).toContain('aria-label="About credit spreads"');
    expect(html).not.toContain("Item 17 of the edge-hypotheses plan");
    expect(help()).toContain("Item 17 of the edge-hypotheses plan tested every setup, side, short and width");
    payload = base([]);
    expect(render()).toContain(">no tested edge<");
    expect(render()).toContain("No setup entered on a recent close");
  });

  it("marks a row from the 45-60 day fallback, and only that row", () => {
    payload = base([row({})]);
    expect(render()).not.toContain(">45-60<");
    payload = base([row({ window: "45-60", expiry: "2026-12-18", dte: 56 })]);
    expect(render()).toContain(">45-60<");
  });

  it("links a card's symbol to its chart", () => {
    payload = base([row({ symbol: "AAA" })]);
    expect(render()).toContain('href="/charts/technicals?symbol=AAA"');
  });

  it("draws a setup as a trade card: the credit for the contract, the max loss as the bar, the order that opens it", () => {
    payload = base([row({})]);
    const html = render();
    expect(html).toContain('class="pos-card tc-card"');
    expect(html).toContain("bull put");
    expect(html).toContain("+$74.00"); // whole contract, a credit +
    expect(html).toContain("0.74 cr"); // the one per-share figure says so
    expect(html).toContain("Cr/ML 0.59");
    expect(html).toContain("max loss $126"); // (2 − 0.74) × 100
    expect(html).toContain("35–40% width");
    expect(html).toContain("1 2026-11-20 96P @ 1.94");
    expect(html).toContain("1 2026-11-20 94P");
    expect(html.match(/is-sell/g)?.length).toBe(1);
    expect(html.match(/is-buy/g)?.length).toBe(1);
    expect(html).toContain("setup: pullback · 2d ago");
    // Each width's credit %, the chain's gaps and the setup's price sit in the lightbox, not on the card.
    expect(html).not.toContain("the chain does not reach this width");
    expect(html).not.toContain("45.0%");
    expect(html).not.toContain("<table");
  });

  it("opens the other widths and the setup's context from the card, in a lightbox", () => {
    payload = base([row({ symbol: "AAA" }), row({ symbol: "BBB" })]);
    const html = render();
    expect(html).toContain("AAA · other widths");
    expect(html).toContain("BBB · other widths");
    expect(html.match(/class="pf-more"/g)).toHaveLength(2);
    // Closed until the button is pressed: nothing of the width table is on the page.
    expect(html).not.toContain("Cr / ML");
    expect(widthsBody(row({}))).toContain("Cr / ML");
    expect(setupBody(row({}))).toContain("pullback · 2d ago");
  });

  it("keeps a row with no 35-40% width, saying so, and hides it only on request", () => {
    payload = base([row({ symbol: "PICK" }), row({ symbol: "NONE", pick: null })]);
    const html = render();
    expect(symbols(html)).toEqual(["NONE", "PICK"]);
    expect(html).toContain("no 35–40% width");
    expect(html).toContain("no width to size");
    expect(help()).toContain("(1 of 2 have no such width)");
    const picked = render("/screeners/credit-spreads?picked=only");
    expect(symbols(picked)).toEqual(["PICK"]);
    expect(picked).toContain("1 with no 35–40% width hidden");
  });

  it("hides earnings in the trade and a thin pick by default, each with an escape; a row with no pick is never called thin", () => {
    payload = base([
      row({ symbol: "KEEP" }),
      row({ symbol: "ERNS", earnings: "in-trade", earningsDate: "2026-11-05" }),
      row({ symbol: "THIN", pick: price({ passes: false }) }),
      row({ symbol: "NOPK", pick: null }),
    ]);
    expect(symbols(render())).toEqual(["KEEP", "NOPK"]);
    const all = render("/screeners/credit-spreads?earnings=all&trade=all");
    expect(symbols(all).slice().sort()).toEqual(["ERNS", "KEEP", "NOPK", "THIN"]);
    expect(all).toContain(">thin<");
  });

  it("filters to one side, and marks a name signalling both", () => {
    payload = base([
      row({ symbol: "PUTS", conflict: true }),
      row({ symbol: "PUTS", spread: "call", conflict: true, setups: ["reversion-short"], signals: [{ setup: "reversion-short", date: "2026-10-23", ago: 0 }], exDividend: "in-trade", exDividendDate: "2026-11-02" }),
      row({ symbol: "ONLY" }),
    ]);
    const html = render();
    expect(html).toContain("both sides");
    expect(html).toContain("bear call");
    expect(html).toContain("ex-div in trade · Nov 2");
    expect(html).toContain("setup: mean reversion (short) · last close");
    const call = row({ symbol: "PUTS", spread: "call", setups: ["reversion-short"], signals: [{ setup: "reversion-short", date: "2026-10-23", ago: 0 }], exDividend: "in-trade", exDividendDate: "2026-11-02" });
    expect(setupBody(call)).toContain("mean reversion (short) · last close");
    expect(setupBody(call)).toContain("in trade · Nov 2");
    const calls = render("/screeners/credit-spreads?side=call");
    expect(symbols(calls)).toEqual(["PUTS"]);
    expect(calls).toContain("2 on the other side");
  });

  it("counts every hidden row once", () => {
    const rows: CreditSpreadRow[] = [];
    for (let m = 0; m < 16; m++) {
      rows.push(
        row({
          symbol: `S${m}`,
          spread: m & 1 ? "call" : "put",
          earnings: m & 2 ? "in-trade" : "clear",
          pick: m & 8 ? null : price({ passes: m & 4 ? false : true }),
        }),
      );
    }
    for (const side of ["both", "put"] as const) {
      for (const pickedOnly of [false, true]) {
        const { shown, hidden } = applyFilters(rows, { side, allEarnings: false, allTrades: false, pickedOnly, q: "" });
        expect(hidden.side + hidden.inTrade + hidden.thin + hidden.unpicked + hidden.unmatched).toBe(rows.length - shown.length);
      }
    }
  });

  it("keeps the builder's symbol order, never a credit order, and offers no sort by credit", () => {
    payload = base([row({ symbol: "AAA", pick: price({ credit: 0.2 }) }), row({ symbol: "ZZZ", pick: price({ credit: 1.5 }) })]);
    expect(symbols(render())).toEqual(["AAA", "ZZZ"]);
    expect(CREDIT_SORTS.map((s) => s.id)).toEqual(["symbol", "maxloss", "width", "expiry"]);
  });

  it("says when a session has no credit-spread list, rather than showing an empty list", () => {
    payload = { ...base([]), built: false };
    const html = render();
    expect(html).toContain("No credit-spread list for this session");
    expect(html).not.toContain("No setup entered on a recent close");
  });

  it("the width table lists every width, marks the pick, and says when the chain does not reach one", () => {
    const html = widthsBody(row({}));
    expect(html).toContain("1 strike");
    expect(html).toContain("5 strikes");
    expect(html).toContain("0.5 × move");
    expect(html).toContain("the chain does not reach this width");
    expect(html.match(/>pick</g)?.length).toBe(1); // the move width shares the pick's long
    expect(html).toContain("45.0%");
  });

  it("draws the max loss as the bar against the largest on the list", () => {
    payload = base([row({ symbol: "BIG", pick: price({ maxLoss: 4 }) }), row({ symbol: "SML", pick: price({ maxLoss: 1 }) })]);
    const html = render();
    expect(html).toContain('style="width:100%"');
    expect(html).toContain('style="width:25%"');
  });

  it("is cards by default and rows on request, the lightbox button staying on the card", () => {
    payload = base([row({ symbol: "AAA" }), row({ symbol: "BBB" })]);
    expect(render()).toContain("tc-grid");
    void writePref("view.credit-spreads", "rows");
    try {
      const html = render();
      expect(html).toContain("tr-list");
      expect(html.match(/class="tr-row"/g)).toHaveLength(2);
      expect(html).not.toContain("other widths"); // the row opens its card in place; the button is on the card
    } finally {
      void writePref("view.credit-spreads", undefined);
    }
  });
});
