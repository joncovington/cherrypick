import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BacktestPathPoint, BacktestTrade, BacktestTradePath } from "@console/shared";
import { TradePathChart, segments } from "../src/pages/Backtest/TradePath";
import { TradeTable } from "../src/pages/Backtest/TradeLog";

/**
 * One MEIC trade's path (2026-10-31): drawn from the module's ledger and the store's quotes, a gap left
 * a gap. Synthetic points only.
 */

function pt(time: string, pnl: number | null, spot: number | null = 4750): BacktestPathPoint {
  return { ts: Number(time.replace(":", "")), time, spot, put_mark: pnl === null ? null : 1, call_mark: 1, pnl, vix: 14 };
}

const PATH: BacktestTradePath = {
  run_id: "20261031-120000-abcdef",
  arm: "d16",
  session: "2024-01-02",
  position_id: "PAPER-d16-SPX-1",
  store: "SPX-60s.db",
  strikes: { put: [4690, 4700], call: [4800, 4810] },
  credit: { put: 1, call: 1.2 },
  entry_time: "2024-01-02T14:00:00-05:00",
  exits: [
    { side: "call", time: "2024-01-02T14:02:00-05:00", reason: "per_side_stop", price: 2.4 },
    { side: "put", time: "2024-01-02T16:00:00-05:00", reason: "expired_settlement", price: 0 },
  ],
  points: [pt("14:00", 0), pt("14:01", null, 4790), pt("14:02", -120), pt("14:03", -40)],
  gaps: 1,
};

describe("segments", () => {
  it("breaks the line at a gap instead of bridging it", () => {
    expect(segments(PATH.points, (p) => p.pnl)).toEqual([
      [[0, 0]],
      [
        [2, -120],
        [3, -40],
      ],
    ]);
    expect(segments([], (p) => p.pnl)).toEqual([]);
  });
});

describe("TradePathChart", () => {
  it("draws P&L and SPX against the four strikes, marks each exit, and says where a leg was not stored", () => {
    const html = renderToString(<TradePathChart path={PATH} />);
    expect(html).toContain("P&amp;L at mid, before any fill model");
    expect(html).toContain("SPX-60s.db");
    expect(html.match(/<polyline /g)).toHaveLength(3); // P&L in two pieces, SPX in one
    expect(html).toContain("short put 4700");
    expect(html).toContain("long call 4810");
    expect(html).toContain("call per_side_stop");
    expect(html).toContain("1 with a leg outside the stored strikes");
  });

  it("says so when no sample falls inside the trade", () => {
    expect(renderToString(<TradePathChart path={{ ...PATH, points: [], gaps: 0 }} />)).toContain("No stored samples");
  });
});

describe("TradeTable", () => {
  const trade: BacktestTrade = {
    session: "2024-01-02",
    entry_time: null,
    exit_time: null,
    outcome: "per_side_stop",
    gross_pnl: -49,
    fees: 1,
    settlement: null,
    slippage: null,
    net_pnl: -50,
    entry_credit: null,
    capital: null,
    position_id: "PAPER-d16-SPX-1",
  };
  const log = { run_id: PATH.run_id, arm: "d16", trades: [trade, { ...trade, position_id: null }] };

  it("offers a path only for a module that has one, and only on a row with a position id", () => {
    expect(renderToString(<TradeTable log={log} />)).not.toContain(">path<");
    expect(renderToString(<TradeTable log={log} pathable />).match(/>path</g)).toHaveLength(1);
  });
});
