import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { cycleExpiry, earningsState, listedQuote, readScreener, tradePrice } from "../src/readers/screeners.js";

/**
 * The Screeners page's reader joins files other packages write and derives two calendar facts. What
 * it must get right: the cycle monthly (45-60 days out, else 30-60); an announcement judged against
 * that monthly with an unreachable date read as unknown, never clear; our rule's pick read from the
 * score file rather than recomputed; a reading matched to the list DAY it read, not the date it was
 * taken; and the liquidity verdict's own spelling (`tradable`).
 */

let tmp: string;
let config: ConsoleConfig;

function write(rel: string, doc: unknown): void {
  const p = path.join(tmp, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(doc));
}

const SP = (sym: string, extra: Record<string, unknown> = {}) => ({
  symbol: sym,
  expiry: "11/13/2026",
  daysToExpiry: 30,
  strikePrice: 98,
  midPrice: 2,
  bidPrice: 1.9,
  askPrice: 2.1,
  impliedVolatilityRank: "41.5%",
  earningsDate: "01/28/2027",
  stockPrice: 100,
  rawReturn: "2.00%",
  annualizedReturn: "24.33%",
  distanceToStrike: "-2.00%",
  ...extra,
});

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-screeners-"));
  config = {
    paths: { technicalsDir: path.join(tmp, "technicals"), marketReportDir: path.join(tmp, "market-report") },
  } as unknown as ConsoleConfig;
  const mr = "market-report";
  write(`${mr}/vendor-screeners/2026-10-14/short-puts.json`, {
    body: {
      shortPuts: [SP("AAA"), SP("BBB", { earningsDate: "10/28/2026" }), SP("CCC"), SP("ETF", { earningsDate: "" })],
      created: "10/14/2026 03:49 PM",
      pricesAt: "10/14/2026 05:16 PM",
    },
  });
  write(`${mr}/vendor-screeners/2026-10-13/short-puts.json`, { body: { shortPuts: [SP("OLD")] } });
  write(`technicals/screeners/2026-10-14.json`, { "short-puts": { picks: { AAA: 98, BBB: 97 } } });
  write(`${mr}/vendor-securities/2026-10-07.json`, { rows: [{ symbol: "ETF", type: "Exchange Traded Fund" }, { symbol: "AAA", type: "Stock" }] });
  write(`${mr}/liquidity/verdicts.json`, { names: { AAA: { verdict: "tradable" }, CCC: { verdict: "illiquid" } } });
  // The broker's date for AAA lands inside the cycle; the list's own (January) would not.
  write(`${mr}/iv/tastytrade.json`, { days: { "2026-10-14": { AAA: { earnings_date: "2026-11-05", earnings_estimated: true } } } });
  // Taken on 10-15, reading the 10-14 list; a later file reading another list day is ignored.
  write(`${mr}/screener-greeks/legs/2026-10-15/1430.json`, {
    list_date: "2026-10-14",
    measured_at: "2026-10-15T18:30:00Z",
    rows: [
      {
        list: "short-puts",
        symbol: "AAA",
        vendor: [{ role: "short", right: "P", expiry: "2026-11-13", strike: 98, quote: { bid: 1, ask: 1.5, open_interest: 3, volume: 1 }, passes: false }],
        monthly: [{ role: "short", right: "P", expiry: "2026-11-20", strike: 98, quote: { bid: 2, ask: 2.1, open_interest: 900, volume: 40 }, passes: true }],
        recommendation: "monthly",
        monthly_passes: true,
      },
    ],
  });
  write(`${mr}/screener-greeks/legs/2026-10-16/1430.json`, { list_date: "2026-10-15", rows: [{ list: "short-puts", symbol: "AAA", vendor: [], monthly: null, recommendation: "none" }] });
});

describe("the cycle monthly and earnings inside it", () => {
  it("takes the latest monthly 45-60 days out, else 30-60, else none", () => {
    expect(cycleExpiry("2026-10-01")).toBe("2026-11-20"); // 50 days
    expect(cycleExpiry("2026-10-14")).toBe("2026-11-20"); // 37: no monthly at 45-60, so 30-60
  });

  it("is in cycle inside [day, expiry], clear only past it, unknown otherwise", () => {
    expect(earningsState(["2026-11-05"], "2026-10-14", "2026-11-20")).toBe("in-cycle");
    expect(earningsState(["2026-11-20"], "2026-10-14", "2026-11-20")).toBe("in-cycle"); // inclusive
    expect(earningsState(["2027-01-28"], "2026-10-14", "2026-11-20")).toBe("clear");
    expect(earningsState(["2026-07-28", null], "2026-10-14", "2026-11-20")).toBe("unknown"); // only a past date
    expect(earningsState(["2026-11-05"], "2026-10-14", null)).toBe("unknown");
  });
});

describe("readScreener", () => {
  it("defaults to the newest list day and lists every saved one", () => {
    const p = readScreener(config, "short-puts");
    expect(p.day).toBe("2026-10-14");
    expect(p.days).toEqual(["2026-10-14", "2026-10-13"]);
    expect(p.scored).toBe(true);
    expect(readScreener(config, "short-puts", "2026-10-13").rows.map((r) => r.symbol)).toEqual(["OLD"]);
  });

  it("joins our pick, the verdict, the earliest coming announcement and the reading of THAT list day", () => {
    const rows = Object.fromEntries(readScreener(config, "short-puts").rows.map((r) => [r.symbol, r]));
    expect(rows["AAA"]!.ours).toEqual([98]);
    expect(rows["BBB"]!.ours).toEqual([97]);
    expect(rows["CCC"]!.ours).toBeNull();
    expect([rows["AAA"]!.liquid, rows["BBB"]!.liquid, rows["CCC"]!.liquid]).toEqual([true, null, false]);
    expect(rows["AAA"]).toMatchObject({ earningsDate: "2026-11-05", earningsSource: "broker", earningsEstimated: true, earnings: "in-cycle" });
    expect(rows["BBB"]).toMatchObject({ earningsDate: "2026-10-28", earningsSource: "list", earnings: "in-cycle" });
    expect(rows["CCC"]).toMatchObject({ earnings: "clear", recommendation: null, readingAt: null });
    // A fund never announces: `none`, where a stock with no date would be `unknown`.
    expect(rows["ETF"]!.earnings).toBe("none");
    expect(rows["AAA"]).toMatchObject({ recommendation: "cycle", cyclePasses: true, cycleExpiry: "2026-11-20", weekly: true, ivRank: 41.5 });
    expect(rows["AAA"]!.listedLegs[0]).toMatchObject({ strike: 98, openInterest: 3, passes: false });
  });

  it("reads fund types from the list the pointer names current, the public one once the vendor is off", () => {
    // The public list says ETF is a stock; with the pointer naming it, ETF loses its `none`.
    write("market-report/securities/2026-10-14.json", { rows: [{ symbol: "ETF", type: "Stock" }] });
    write("market-report/securities/current.json", { source: "vendor", day: "2026-10-07" });
    const fund = (): string => Object.fromEntries(readScreener(config, "short-puts").rows.map((r) => [r.symbol, r]))["ETF"]!.earnings;
    expect(fund()).toBe("none");
    write("market-report/securities/current.json", { source: "public", day: "2026-10-14" });
    expect(fund()).not.toBe("none");
  });

  it("judges earnings against the trade's expiry: the later of listed and cycle until a reading says which", () => {
    // The list's 11/27 weekly spans an 11/24 report that the 11/20 cycle monthly ends before.
    write("market-report/vendor-screeners/2026-10-16/credit-spreads.json", {
      body: {
        creditSpreads: [
          { symbol: "UNR", type: "Put", price: 262, strike: { sell: 260, buy: 240 }, expiry: "11/27/2026", earningsDate: "11/24/2026 (39) AM" },
          { symbol: "MON", type: "Put", price: 262, strike: { sell: 260, buy: 240 }, expiry: "11/27/2026", earningsDate: "11/24/2026 (39) AM" },
          { symbol: "LST", type: "Put", price: 262, strike: { sell: 260, buy: 240 }, expiry: "11/27/2026", earningsDate: "11/24/2026 (39) AM" },
        ],
      },
    });
    const legs = (sym: string, recommendation: string) => ({
      list: "credit-spreads",
      symbol: sym,
      vendor: [],
      monthly: [{ role: "short", right: "P", expiry: "2026-11-20", strike: 260, quote: {}, passes: true }],
      recommendation,
      monthly_passes: true,
    });
    write("market-report/screener-greeks/legs/2026-10-19/1130.json", {
      list_date: "2026-10-16",
      measured_at: "2026-10-19T15:30:00Z",
      rows: [legs("MON", "monthly"), legs("LST", "vendor")],
    });
    const rows = Object.fromEntries(readScreener(config, "credit-spreads", "2026-10-16").rows.map((r) => [r.symbol, r]));
    expect(rows["UNR"]).toMatchObject({ cycleExpiry: "2026-11-20", earningsExpiry: "2026-11-27", earnings: "in-cycle" });
    expect(rows["MON"]).toMatchObject({ earningsExpiry: "2026-11-20", earnings: "clear" });
    expect(rows["LST"]).toMatchObject({ earningsExpiry: "2026-11-27", earnings: "in-cycle" });
  });

  it("judges earnings, days and spot from the reading's day, a before-the-open report that day behind the trade", () => {
    // The 10-20 list, read at 14:30 ET on 10-21 (COF reported 10-20; PM 10-21 before the open).
    // Judged from the list day, both read in cycle.
    const cs = (symbol: string, earningsDate: string, sell = 200, buy = 190, type = "Put") => ({
      symbol,
      type,
      price: 201.47,
      strike: { sell, buy },
      expiry: "11/27/2026",
      earningsDate,
      premium: { value: 3.4, width: 10, percentage: "34%" },
    });
    write("market-report/vendor-screeners/2026-10-20/credit-spreads.json", {
      body: {
        creditSpreads: [
          cs("COF", "10/20/2026 (1) PM", 205, 215, "Call"),
          cs("PM", "10/21/2026 (1) AM"),
          cs("UNT", "10/21/2026 (1) AM"),
          cs("AMC", "10/21/2026 (1) PM"),
        ],
      },
    });
    write("market-report/iv/tastytrade.json", {
      days: {
        "2026-10-20": {
          COF: { earnings_date: "2026-10-20", earnings_time: null },
          PM: { earnings_date: "2026-10-21", earnings_time: "BTO" },
          UNT: { earnings_date: "2026-10-21", earnings_time: null },
          AMC: { earnings_date: "2026-10-21", earnings_time: "AMC" },
        },
      },
    });
    const vendorLegs = (sym: string, right: string, sell: number, buy: number) => ({
      list: "credit-spreads",
      symbol: sym,
      vendor: [
        { role: "short", right, expiry: "2026-11-27", strike: sell, quote: { bid: 1, ask: 1.1 }, passes: true },
        { role: "long", right, expiry: "2026-11-27", strike: buy, quote: { bid: 0.5, ask: 0.6 }, passes: true },
      ],
      monthly: [],
      recommendation: "vendor",
      vendor_prices: { mid: 5.5, working: 5.38, natural: 4.9, cost: 0.6 },
    });
    write("market-report/screener-greeks/legs/2026-10-21/1430.json", {
      list_date: "2026-10-20",
      measured_at: "2026-10-21T18:30:11Z",
      rows: [vendorLegs("COF", "C", 205, 215), vendorLegs("PM", "P", 200, 190)],
    });
    write("market-report/screener-greeks/income/2026-10-21.json", {
      taken_at: "2026-10-21T18:45:00Z",
      symbols: { COF: { spot: 208.375 }, PM: { spot: 201 } },
    });
    const p = readScreener(config, "credit-spreads", "2026-10-20");
    expect(p.tradeDay).toBe("2026-10-21");
    expect(p.spotAt).toBe("2026-10-21T18:45:00Z");
    const rows = Object.fromEntries(p.rows.map((r) => [r.symbol, r]));
    expect(rows["COF"]!.earnings).not.toBe("in-cycle"); // reported the day before the reading
    expect(rows["PM"]!.earnings).not.toBe("in-cycle"); // before the open on the reading's day
    expect(rows["UNT"]!.earnings).toBe("in-cycle"); // untimed on the reading's day: kept in, the safe way
    expect(rows["AMC"]!.earnings).toBe("in-cycle"); // after the close: still ahead
    expect(rows["PM"]!.earningsFrom).toBe("2026-10-21");
    // Days to expiry from the reading's day (37), not the list day (38).
    expect(rows["COF"]!.trade).toMatchObject({ dte: 37, spot: 208.375, shortInTheMoney: true });
    expect(rows["PM"]!.trade).toMatchObject({ shortInTheMoney: false });
  });

  it("an empty home is an empty list, not an error", () => {
    fs.rmSync(tmp, { recursive: true, force: true });
    expect(readScreener(config, "credit-spreads")).toMatchObject({ day: null, days: [], rows: [] });
  });
});

describe("the trade's price", () => {
  const prices = { mid: 2.0, working: 1.95, natural: 1.9, cost: 0.1 };
  const cycle = [{ role: "short" as const, right: "P" as const, expiry: "2026-12-18", strike: 98, bid: 1, ask: 1.1, openInterest: 500, volume: 5, passes: true, quiet: true }];

  it("prices the side the reading recommends, annualised on the share price as the list does", () => {
    const listed = tradePrice({ recommendation: "vendor", vendor_prices: prices, monthly_prices: { ...prices, working: 9 } }, "2026-11-13", cycle, "2026-10-14", 100, null)!;
    expect(listed).toMatchObject({ side: "listed", expiry: "2026-11-13", dte: 30, working: 1.95, workingCreditPct: null });
    expect(listed.workingAnnualisedPct).toBeCloseTo((1.95 / 100) * (365 / 30) * 100, 6);
    const monthly = tradePrice({ recommendation: "monthly", vendor_prices: prices, monthly_prices: { ...prices, working: 2.5 } }, "2026-11-13", cycle, "2026-10-14", 100, null)!;
    expect(monthly).toMatchObject({ side: "cycle", expiry: "2026-12-18", dte: 65, working: 2.5 });
  });

  it("states a spread's working credit against its width, not an annual return", () => {
    const t = tradePrice({ recommendation: "vendor", vendor_prices: { mid: 2, working: 1.8, natural: 1.6, cost: 0.4 } }, "2026-11-27", null, "2026-10-14", 380, 5)!;
    expect(t.workingCreditPct).toBeCloseTo(36, 6);
    expect(t.workingAnnualisedPct).toBeNull();
  });

  it("prices nothing without a reading, a recommendation or a priced side", () => {
    expect(tradePrice(undefined, "2026-11-13", cycle, "2026-10-14", 100, null)).toBeNull();
    expect(tradePrice({ recommendation: "none" }, "2026-11-13", cycle, "2026-10-14", 100, null)).toBeNull();
    expect(tradePrice({ recommendation: "monthly", monthly_prices: null }, "2026-11-13", cycle, "2026-10-14", 100, null)).toBeNull();
  });

  it("flags a listed quote that cannot back its mid", () => {
    expect(listedQuote(0, 4.9)).toBe("no-bid"); // IYR 2026-10-16: 32.8% annualised on no bid
    expect(listedQuote(0.3, 1.2)).toBe("wide"); // 0.90 wide on a 0.75 mid
    expect(listedQuote(0.39, 1.16)).toBeNull(); // 99% of its mid: wide, but not past it
    expect(listedQuote(1.9, 2.1)).toBeNull();
    expect(listedQuote(null, null)).toBeNull();
  });
});
