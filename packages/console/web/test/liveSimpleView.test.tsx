import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { LiveMeicOpenRow, ManageAccount } from "@console/shared";
import { Cells, HeadRow, columnsFor, type Column } from "../src/components/columns";
import { NAV_DECL, viewSlides } from "../src/lightbox/navGroups";
import { AccountTiles } from "../src/pages/Live/ManagePage";
import { OPEN_COLUMNS, ORDER_COLUMNS, type AttentionRow } from "../src/pages/Live/MeicLivePage";
import { aligned, inView, tables } from "./viewTables";

/**
 * The Live page's simple view (2026-10-21): the columns, tiles and tabs it leaves to detail, and
 * the ones removed from both. The per-tab tables are pinned beside their own fixtures (positions in
 * manageHeldForCall, portfolio and income in managePortfolioCards, today in liveTodayPage, wheel and
 * pmcc in their cards' tests); this file holds the mechanism, meic, the header tiles and the rail.
 */

function table<R>(cols: Column<R>[], rows: R[]): string {
  return renderToString(
    <table>
      <thead>
        <HeadRow cols={cols} />
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <Cells cols={cols} row={r} />
          </tr>
        ))}
      </tbody>
    </table>,
  );
}

describe("columnsFor", () => {
  const cols = [{ key: "a" }, { key: "b", detailOnly: true }, { key: "c", detailOnly: false }];

  it("keeps every column in detail and drops the detail-only ones in simple", () => {
    expect(columnsFor(cols, "detail").map((c) => c.key)).toEqual(["a", "b", "c"]);
    expect(columnsFor(cols, "simple").map((c) => c.key)).toEqual(["a", "c"]);
  });

  it("the alignment check fires on a row that has drifted from its headers", () => {
    const broken = renderToString(
      <table>
        <thead>
          <tr>
            <th>a</th>
            <th>b</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>1</td>
          </tr>
        </tbody>
      </table>,
    );
    expect(aligned(tables(broken)[0]!)).toBe(false);
  });
});

const openRow: LiveMeicOpenRow = {
  id: 1,
  tradeDate: "2026-10-21",
  entryTime: "2026-10-21T12:30:00",
  symbol: "SPX",
  arm: "pm-200",
  status: "open",
  working: false,
  putStrike: 6600,
  callStrike: 6800,
  wingWidth: 10,
  quantity: 1,
  netCredit: 1.2,
  entryCash: 120,
  putHeld: true,
  callHeld: true,
  putClosing: false,
  callClosing: false,
  fillConfirmedAt: null,
  overdue: false,
};

const orphan: AttentionRow = { key: "o", kind: "orphan", order: "123", status: "Live", quietStatus: false, symbol: "SPX", ext: "meic-abc" };

describe("Live → meic, simple and detail", () => {
  it("keeps date, sym and arm on the open condors to detail", () => {
    const simple = tables(table(columnsFor(OPEN_COLUMNS, "simple"), [openRow]))[0]!;
    const detail = tables(table(columnsFor(OPEN_COLUMNS, "detail"), [openRow]))[0]!;
    for (const h of ["date", "sym", "arm"]) {
      expect(simple.headers).not.toContain(h);
      expect(detail.headers).toContain(h);
    }
    expect(simple.headers).toEqual(["time", "put", "call", "wing", "qty", "price", "entry", "state", "sides held"]);
    expect(aligned(simple)).toBe(true);
    expect(aligned(detail)).toBe(true);
  });

  it("an overdue condor still says so in simple, where its date is hidden", () => {
    const html = table(columnsFor(OPEN_COLUMNS, "simple"), [{ ...openRow, overdue: true }]);
    expect(html).toContain("open · overdue");
  });

  it("keeps the external id on the orders needing a person to detail", () => {
    expect(tables(table(columnsFor(ORDER_COLUMNS, "simple"), [orphan]))[0]!.headers).toEqual(["kind", "order", "status", "symbol"]);
    expect(tables(table(columnsFor(ORDER_COLUMNS, "detail"), [orphan]))[0]!.headers).toContain("external id");
  });
});

const acct: ManageAccount = {
  account: "****1234",
  netLiq: 100000,
  maintenance: null,
  buyingPower: null,
  riskBudget: 3000,
  pnlGross: 250,
  counts: {},
  trades: [],
  excluded: [],
  portfolio: null,
  wheel: null,
  pmcc: null,
};

describe("the evaluation's header tiles", () => {
  it("keep the per-trade budget and review line to detail, and have no holding count in either view", () => {
    const simple = inView("simple", () => renderToString(<AccountTiles a={acct} oversize={2} />));
    const detail = inView("detail", () => renderToString(<AccountTiles a={acct} oversize={2} />));
    for (const hidden of ["per-trade budget", "review over"]) {
      expect(simple).not.toContain(hidden);
      expect(detail).toContain(hidden);
    }
    expect(simple).not.toContain(">holding<");
    expect(detail).not.toContain(">holding<");
    // The account carries no net total, so the tile stays gross and says so.
    expect(detail).toContain("mark P&amp;L (gross, before fees)");
    expect(simple).toContain("tile-grid-3");
    expect(detail).toContain("tile-grid-5");
  });
});

describe("the Live rail", () => {
  it("leaves the flies tab out of the simple view and keeps it in detail", () => {
    const slides = NAV_DECL.live.slides;
    expect(viewSlides("live", slides, "simple").map((s) => s.id)).not.toContain("flies");
    expect(viewSlides("live", slides, "simple").map((s) => s.id)).toEqual(["today", "positions", "portfolio", "wheel", "pmcc", "meic"]);
    expect(viewSlides("live", slides, "detail").map((s) => s.id)).toContain("flies");
  });
});
