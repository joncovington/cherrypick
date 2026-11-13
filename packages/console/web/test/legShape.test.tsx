import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { shapeOrder } from "../src/lib/legShape";
import { TradeCard, type TradeCardModel } from "../src/pages/Live/TradeCard";

const order = (legs: string[]) =>
  shapeOrder(legs, (l) => ({ action: l.slice(0, 3), text: l.slice(4) }));

describe("legs in the structure's shape", () => {
  it("draws each structure low strike up, puts before calls", () => {
    expect(order(["STO 1 2026-12-18 585P", "STO 1 2026-12-18 709C", "BTO 1 2026-12-18 575P", "BTO 1 2026-12-18 719C"])).toEqual([
      "BTO 1 2026-12-18 575P",
      "STO 1 2026-12-18 585P",
      "STO 1 2026-12-18 709C",
      "BTO 1 2026-12-18 719C",
    ]);
    expect(order(["STO 2 100P", "BTO 1 110P", "BTO 1 90P"])).toEqual(["BTO 1 90P", "STO 2 100P", "BTO 1 110P"]);
    expect(order(["STO 1 100C", "STO 1 100P", "BTO 1 110C", "BTO 1 90P"])).toEqual(["BTO 1 90P", "STO 1 100P", "STO 1 100C", "BTO 1 110C"]);
    expect(order(["STO 1 6800C", "STO 1 6600P"])).toEqual(["STO 1 6600P", "STO 1 6800C"]);
  });

  it("puts a calendar's front expiry first and a roll's closes before its opens", () => {
    expect(order(["BTO 1 2027-01-15 100C", "STO 1 2026-12-18 100C"])).toEqual(["STO 1 2026-12-18 100C", "BTO 1 2027-01-15 100C"]);
    expect(order(["STO 1 2027-01-15 95P", "BTO 1 2027-01-15 90P", "BTC 1 2026-12-18 100P", "STC 1 2026-12-18 95P"])).toEqual([
      "STC 1 2026-12-18 95P",
      "BTC 1 2026-12-18 100P",
      "BTO 1 2027-01-15 90P",
      "STO 1 2027-01-15 95P",
    ]);
  });

  it("keeps a leg with no strike after the options, in the order it came", () => {
    expect(order(["BTO 100 shares", "STO 1 105C", "BTO 1 95P", "STC 50 shares"])).toEqual(["STC 50 shares", "BTO 1 95P", "STO 1 105C", "BTO 100 shares"]);
  });

  it("draws a trade card's legs in shape whatever order its model holds them", () => {
    const m: TradeCardModel = {
      key: "k",
      symbol: "QQQ",
      spot: null,
      shape: "iron condor",
      chips: null,
      cash: 332,
      side: [],
      bar: null,
      barTitle: "",
      label: "",
      open: { date: "proposed", price: "3.32 cr" },
      legs: [
        { buy: false, text: "1 2026-12-18 585P", quote: null },
        { buy: false, text: "1 2026-12-18 709C", quote: null },
        { buy: true, text: "1 2026-12-18 575P", quote: null },
        { buy: true, text: "1 2026-12-18 719C", quote: null },
      ],
      after: [],
      foot: ["", ""],
    };
    const html = renderToString(<TradeCard m={m} />);
    const at = (s: string) => html.indexOf(s);
    expect(at("575P")).toBeLessThan(at("585P"));
    expect(at("585P")).toBeLessThan(at("709C"));
    expect(at("709C")).toBeLessThan(at("719C"));
  });
});
