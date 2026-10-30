import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { CreditSpreadPayload, CreditSpreadPrice, CreditSpreadRow } from "@console/shared";

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

const { CreditSpreadTab, WidthTable, applyFilters } = await import("../src/pages/Screeners/CreditSpreadTab");

/** A row's expanded line: its setups, price, fair value and ex-dividend, then its widths. */
function expanded(r: CreditSpreadRow): string {
  return renderToString(
    <MemoryRouter>
      <table>
        <tbody>
          <WidthTable r={r} colSpan={10} />
        </tbody>
      </table>
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

function render(url = "/screeners/credit-spreads"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <CreditSpreadTab />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

describe("our credit-spread list", () => {
  it("always says no edge was found, with rows and without", () => {
    payload = base([row({})]);
    expect(render()).toContain("No tested edge.");
    expect(render()).toContain(">no tested edge<");
    payload = base([]);
    expect(render()).toContain("No tested edge.");
    expect(render()).toContain("No setup entered on a recent close");
  });

  it("marks a row from the 45-60 day fallback, and only that row", () => {
    payload = base([row({})]);
    expect(render()).not.toContain(">45-60<");
    payload = base([row({ window: "45-60", expiry: "2026-12-18", dte: 56 })]);
    expect(render()).toContain(">45-60<");
  });

  it("shows the pick as a credit per share and its max loss per contract", () => {
    payload = base([row({})]);
    const html = render();
    expect(html).toContain("bull put");
    expect(html).toContain("96P");
    expect(html).toContain("0.74 cr");
    expect(html).toContain("$126"); // (2 − 0.74) × 100
    // The setups and each width's credit % sit in the row's expanded line since 2026-10-30.
    expect(html).not.toContain("pullback · 2d ago");
    const line = expanded(row({}));
    expect(line).toContain("pullback · 2d ago");
    expect(line).toContain("37.0%");
  });

  it("keeps a row with no 35-40% width, saying so, and hides it only on request", () => {
    payload = base([row({ symbol: "PICK" }), row({ symbol: "NONE", pick: null })]);
    const html = render();
    expect(html).toContain("NONE");
    expect(html).toContain("no 35–40% width");
    expect(html).toContain("(1 of 2 have no such width)");
    const picked = render("/screeners/credit-spreads?picked=only");
    expect(picked).toContain("PICK");
    expect(picked).not.toContain("NONE");
    expect(picked).toContain("1 with no 35–40% width hidden");
  });

  it("hides earnings in the trade and a thin pick by default, each with an escape; a row with no pick is never called thin", () => {
    payload = base([
      row({ symbol: "KEEP" }),
      row({ symbol: "ERNS", earnings: "in-trade", earningsDate: "2026-11-05" }),
      row({ symbol: "THIN", pick: price({ passes: false }) }),
      row({ symbol: "NOPK", pick: null }),
    ]);
    const html = render();
    expect(html).toContain("KEEP");
    expect(html).toContain("NOPK");
    expect(html).not.toContain("ERNS");
    expect(html).not.toContain("THIN");
    const all = render("/screeners/credit-spreads?earnings=all&trade=all");
    for (const s of ["KEEP", "ERNS", "THIN", "NOPK"]) expect(all).toContain(s);
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
    const call = row({ symbol: "PUTS", spread: "call", setups: ["reversion-short"], signals: [{ setup: "reversion-short", date: "2026-10-23", ago: 0 }], exDividend: "in-trade", exDividendDate: "2026-11-02" });
    expect(expanded(call)).toContain("mean reversion (short) · last close");
    expect(expanded(call)).toContain("in trade · Nov 2");
    const calls = render("/screeners/credit-spreads?side=call");
    expect(calls).not.toContain("ONLY");
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

  it("keeps the builder's symbol order, never a credit order", () => {
    payload = base([row({ symbol: "AAA", pick: price({ credit: 0.2 }) }), row({ symbol: "ZZZ", pick: price({ credit: 1.5 }) })]);
    const html = render();
    expect(html.indexOf("AAA")).toBeLessThan(html.indexOf("ZZZ"));
  });

  it("says when a session has no credit-spread list, rather than showing an empty table", () => {
    payload = { ...base([]), built: false };
    const html = render();
    expect(html).toContain("No credit-spread list for this session");
    expect(html).not.toContain("No setup entered on a recent close");
  });

  it("the width table lists every width, marks the pick, and says when the chain does not reach one", () => {
    const html = renderToString(
      <table>
        <tbody>
          <WidthTable r={row({})} colSpan={15} />
        </tbody>
      </table>,
    ).replace(/<!-- -->/g, "");
    expect(html).toContain("1 strike");
    expect(html).toContain("5 strikes");
    expect(html).toContain("0.5 × move");
    expect(html).toContain("the chain does not reach this width");
    expect(html.match(/>pick</g)?.length).toBe(1); // the move width shares the pick's long
    expect(html).toContain("45.0%");
  });
});
