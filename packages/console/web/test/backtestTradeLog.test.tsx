import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BacktestTrade } from "@console/shared";
import { TradeLog, TradeTable, arrangeTrades } from "../src/pages/Backtest/TradeLog";

/**
 * A run arm's trade log (2026-10-31): the module's trades, reordered and filtered here, never
 * recomputed. Synthetic trades only.
 */

function trade(over: Partial<BacktestTrade>): BacktestTrade {
  return {
    session: "2024-01-02",
    entry_time: "2024-01-02T14:00:00",
    exit_time: "2024-01-02T16:00:00",
    outcome: "expired_settlement",
    gross_pnl: 21,
    fees: 1,
    settlement: null,
    slippage: null,
    net_pnl: 20,
    entry_credit: 100,
    capital: 400,
    position_id: "a",
    ...over,
  };
}

const TRADES = [
  trade({ position_id: "a", net_pnl: 20 }),
  trade({ position_id: "b", net_pnl: -50, outcome: "per_side_stop" }),
  trade({ position_id: "c", net_pnl: null, outcome: null }),
  trade({ position_id: "d", net_pnl: 5 }),
];

describe("arrangeTrades", () => {
  it("keeps the module's order by close, and filters by the sign of the net", () => {
    expect(arrangeTrades(TRADES, "closed", "all").map((t) => t.position_id)).toEqual(["a", "b", "c", "d"]);
    expect(arrangeTrades(TRADES, "closed", "winners").map((t) => t.position_id)).toEqual(["a", "d"]);
    expect(arrangeTrades(TRADES, "closed", "losers").map((t) => t.position_id)).toEqual(["b"]);
  });

  it("sorts best or worst first, an unrecorded net last either way, never as zero", () => {
    expect(arrangeTrades(TRADES, "net-desc", "all").map((t) => t.position_id)).toEqual(["a", "d", "b", "c"]);
    expect(arrangeTrades(TRADES, "net-asc", "all").map((t) => t.position_id)).toEqual(["b", "d", "a", "c"]);
  });
});

describe("TradeLog", () => {
  it("stays closed until asked, so the report reads without the trades", () => {
    const html = renderToString(<TradeLog runId="20261017-093000-a1b2c3" arm="pm-200" />);
    expect(html).toContain("every trade");
    expect(html).not.toContain("<table");
  });

  it("shows each trade's own exit reason and money, and drops a column no trade records", () => {
    const html = renderToString(<TradeTable log={{ run_id: "r", arm: "pm-200", trades: TRADES }} />);
    expect(html).toContain("per_side_stop");
    expect(html).toContain("not recorded");
    expect(html).toContain("2024-01-02 14:00");
    expect(html).toContain("4 of 4 trades");
    expect(html).toContain("at risk");
    expect(html).not.toContain("slippage");
    expect(html).not.toContain("settlement</th>");
  });
});
