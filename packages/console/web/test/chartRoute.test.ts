import { describe, it, expect } from "vitest";
import type { FuturesTickerEntry } from "@console/shared";
import { chartSymbol, dailyReason, intradayReason, movedChartUrl, resolveTimeframe } from "../src/pages/Chart/chartRoute";
import { liveEntryFor } from "../src/pages/Intraday/liveSymbols";
import { isProfileSymbol } from "../src/pages/Profile/ProfilePage";

/**
 * The Charts page's one chart: which symbol and timeframe a URL draws, why a timeframe is disabled,
 * and where the old intraday and technicals URLs land.
 */

const ES: FuturesTickerEntry = {
  product: "ES",
  label: "/ES",
  name: "E-mini S&P 500",
  contract: "ESZ6",
  streamerSymbol: "/ESZ26:XCME",
  expiration: null,
  reason: null,
};

describe("the one chart's symbol", () => {
  it("opens on /ES, and reads any other name in upper case", () => {
    expect([null, "", "  ", " msft ", "/es", "SPX"].map(chartSymbol)).toEqual(["/ES", "/ES", "/ES", "MSFT", "/ES", "SPX"]);
  });

  it("is live for a future with its slash and for the four cash symbols, and for nothing else", () => {
    expect(liveEntryFor("/ES", [ES])).toBe(ES);
    expect(liveEntryFor("ES", [ES])).toBeUndefined(); // a stock ticker ES is not the future
    expect(liveEntryFor("SPX", [])?.streamerSymbol).toBe("SPX");
    expect(liveEntryFor("MSFT", [ES])).toBeUndefined();
  });
});

describe("the one chart's timeframe", () => {
  it("opens a live symbol on 5m and any other on daily", () => {
    expect(resolveTimeframe(null, true, false)).toBe("5m");
    expect(resolveTimeframe(null, false, true)).toBe("1d");
    expect(resolveTimeframe("weekly", true, true)).toBe("5m");
  });

  it("keeps an intraday width only while the feed carries the symbol", () => {
    expect(resolveTimeframe("15m", true, false)).toBe("15m");
    expect(resolveTimeframe("15m", false, true)).toBe("1d");
  });

  it("keeps daily unless the symbol is known to have no daily chart", () => {
    expect(resolveTimeframe("1d", true, true)).toBe("1d");
    expect(resolveTimeframe("1d", true, null)).toBe("1d"); // the index is still being read
    expect(resolveTimeframe("1d", true, false)).toBe("5m");
    expect(resolveTimeframe("1d", false, false)).toBe("1d"); // nothing else to fall to; the page says why
  });

  it("says why daily is off: the feature, a future, or a name the report job does not chart", () => {
    const charted = new Set(["SPX", "MSFT"]);
    expect(dailyReason("MSFT", false, charted)).toMatch(/technicals is off/);
    expect(dailyReason("/ES", true, charted)).toMatch(/not futures such as \/ES/);
    expect(dailyReason("ZZZZ", true, charted)).toMatch(/no daily chart for ZZZZ/);
    expect(dailyReason("MSFT", true, charted)).toBeNull();
    expect(dailyReason("ZZZZ", true, undefined)).toBeNull(); // not known yet
  });

  it("says why intraday is off by naming what the feed carries", () => {
    expect(intradayReason(true, ["SPX", "/ES"])).toBeNull();
    expect(intradayReason(false, ["SPX", "/ES"])).toBe("the live feed carries SPX, /ES only");
  });
});

describe("the old chart URLs", () => {
  it("send the intraday chart's product and width to the one chart, with every other key", () => {
    expect(movedChartUrl("intraday", "?product=NQ&period=15m&profile=off&hide=ib&sessions=10")).toBe(
      "/charts/chart?symbol=/NQ&tf=15m&profile=off&hide=ib&sessions=10",
    );
    expect(movedChartUrl("intraday", "?product=SPX")).toBe("/charts/chart?symbol=SPX");
    expect(movedChartUrl("intraday", "")).toBe("/charts/chart?symbol=/ES");
    expect(movedChartUrl("intraday", "?product=ES&period=weekly")).toBe("/charts/chart?symbol=/ES");
  });

  it("send the technicals chart to the daily view, SPX when it named no symbol", () => {
    expect(movedChartUrl("technicals", "?symbol=msft&setup=trend&side=short&levels=all")).toBe(
      "/charts/chart?symbol=MSFT&tf=1d&setup=trend&side=short&levels=all",
    );
    expect(movedChartUrl("technicals", "")).toBe("/charts/chart?symbol=SPX&tf=1d");
  });

  it("send the profile tab to the sessions panel under the 15-minute chart, its sessions as days", () => {
    // The morning pack's link: a symbol and the session to open.
    expect(movedChartUrl("profile", "?symbol=/ES&day=2026-11-13")).toBe("/charts/chart?symbol=/ES&tf=15m&day=2026-11-13");
    // `sessions` was the tab's day count; on the chart it counts histograms, so it becomes `days`.
    expect(movedChartUrl("profile", "?symbol=QQQ&sessions=20&composite=10&measure=volume")).toBe(
      "/charts/chart?symbol=QQQ&tf=15m&days=20&composite=10&measure=volume",
    );
    expect(movedChartUrl("profile", "")).toBe("/charts/chart?symbol=SPX&tf=15m");
  });
});

describe("the sessions panel", () => {
  it("is under the chart only for the symbols the profile-bars job records", () => {
    expect(["SPX", "SPY", "NDX", "QQQ", "/ES", "/NQ"].every(isProfileSymbol)).toBe(true);
    expect(["/CL", "/GC", "ES", "MSFT", null].some(isProfileSymbol)).toBe(false);
  });
});
