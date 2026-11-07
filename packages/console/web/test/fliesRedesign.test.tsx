import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { FliesPositionRow } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { scopeChanged, scopeParts, NO_SCOPE } = await import("../src/pages/Flies/FliesFilter");
const { positionShown, positionModel, PositionsCard, BooksCard } = await import("../src/pages/Flies/FliesTables");
const { logChoice, logModel } = await import("../src/pages/Flies/HistoryTab");

const meta = {
  eras: [
    { era: "a", label: "current era", trades: 5 },
    { era: "b", label: "old era", trades: 3 },
  ],
  currentEra: "a",
  dates: ["2026-11-06", "2026-11-05"],
};

const pos = (over: Partial<FliesPositionRow>): FliesPositionRow => ({
  mode: "paper", positionId: "p1", tradeDate: "2026-11-06", symbol: "XYZ", arm: "control", entryMode: "credit-first",
  kind: "fly", side: "call", center: 5890, wingWidth: 10, farWidth: null, quantity: 1, net: 1.1, entryCash: 110,
  floorDollars: 0, riskFree: true, status: "settled", pnl: 40, entryTime: "2026-11-06T10:42:00-05:00", ...over,
});

const html = (el: React.ReactElement) => renderToString(<MemoryRouter>{el}</MemoryRouter>).replace(/<!--\s*-->/g, "");

describe("the module's filter and sort", () => {
  it("counts what is off its default and says what is on", () => {
    expect(scopeChanged(NO_SCOPE)).toBe(0);
    expect(scopeChanged({ arm: "control", symbol: null, era: "ALL", date: "2026-11-05" })).toBe(3);
    expect(scopeParts(NO_SCOPE, meta)).toEqual(["all arms", "current era", "latest day · 11-06"]);
    expect(scopeParts({ ...NO_SCOPE, era: "ALL" }, meta)).toContain("all eras, pooled");
  });
});

describe("positions", () => {
  it("filters at risk and risk-free", () => {
    expect(positionShown(pos({}), "free")).toBe(true);
    expect(positionShown(pos({}), "risk")).toBe(false);
    expect(positionShown(pos({ riskFree: false }), "risk")).toBe(true);
  });
  it("is a trade card with the entry cash signed and the symbol linked to its chart", () => {
    const m = positionModel(pos({}));
    expect(m.cash).toBe(110);
    expect(m.symbolHref).toBe("/charts/technicals?symbol=XYZ");
    expect(m.shape).toContain("5890");
  });
  it("draws the order chain from the row's structure, at its quantity", () => {
    const m = positionModel(pos({ quantity: 2 }));
    expect(m.legs.map((l) => `${l.buy ? "BTO" : "STO"} ${l.text}`)).toEqual(["BTO 2 5880C", "STO 4 5890C", "BTO 2 5900C"]);
    expect(positionModel(pos({ kind: "bwb", side: "put", wingWidth: 10, farWidth: 20 })).legs.map((l) => l.text)).toEqual(["1 5870P", "2 5890P", "1 5900P"]);
  });
  it("opens on cards by default", () => {
    const data = { positions: { rows: [pos({})], total: 1, offset: 0, limit: 100 }, books: { rows: [], total: 0, offset: 0, limit: 100 } } as never;
    const out = html(<PositionsCard data={data} paging={{ page: { offset: 0, limit: 100 }, setOffset: () => {}, setLimit: () => {} }} loading={false} isError={false} busy={false} narrowed={false} />);
    expect(out).toContain("tc-grid");
    expect(out).not.toContain("tr-list");
  });
});

describe("books", () => {
  it("keeps the explanation behind the ? and out of the card", () => {
    const data = { positions: { rows: [], total: 0, offset: 0, limit: 100 }, books: { rows: [], total: 0, offset: 0, limit: 100 } } as never;
    const out = html(<BooksCard data={data} paging={{ page: { offset: 0, limit: 100 }, setOffset: () => {}, setLimit: () => {} }} loading={false} isError={false} busy={false} narrowed={false} />);
    expect(out).toContain("About books");
    expect(out).not.toContain("was not recorded");
  });
});

describe("the trade log", () => {
  it("reads the server's sort back as a panel choice", () => {
    expect(logChoice(undefined, undefined)).toEqual({ id: "date", reversed: false });
    expect(logChoice("net", "asc")).toEqual({ id: "net", reversed: false });
    expect(logChoice("net", "desc")).toEqual({ id: "net", reversed: true });
  });
  it("gives fees, settlement and slippage their own lines on a card", () => {
    const m = logModel({
      tradeDate: "2026-11-06", entryTime: null, symbol: "XYZ", arm: "control", entryMode: null, kind: "fly", side: null, center: 5890,
      wingWidth: 10, farWidth: null, window: null, quantity: 1, price: 1.1, entryCash: 110, exitCash: -20, exitKind: "settled", gross: 90,
      fees: 4, settlementFees: null, slippage: null, pnl: 86, latencyMin: null, pinned: false,
    });
    expect(m.after).toEqual(["fees $4.00", "settlement n/r", "slippage n/r"]);
  });
});
