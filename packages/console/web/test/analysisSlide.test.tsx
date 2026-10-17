import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ArmAnalysis, CapitalReading, DrawdownEpisode, ExecutionReading } from "@console/shared";
import { ArmCard, CapitalTiles, ExecutionTable } from "../src/components/analysis/AnalysisSlide";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

const episode = (over: Partial<DrawdownEpisode> = {}): DrawdownEpisode => ({
  peak_session: "2026-10-01",
  peak_equity: 100,
  low_session: "2026-10-05",
  low_equity: 40,
  depth: 60,
  recovered_session: "2026-10-07",
  open: false,
  sessions_falling: 2,
  sessions_recovering: 2,
  days_falling: 4,
  days_recovering: 2,
  ...over,
});

function arm(over: Partial<ArmAnalysis> = {}): ArmAnalysis {
  return {
    version: 1,
    reading: {},
    best_trade: { net: 120, session: "2026-10-01", entry_time: null },
    worst_trade: { net: -80, session: "2026-10-05", entry_time: null },
    streaks: { longest_win: 22, longest_loss: 5, current: { kind: "loss", length: 1 } },
    session_streaks: { longest_win: 3, longest_loss: 2, current: { kind: "loss", length: 1 } },
    drawdowns: { count: 1, open: null, deepest: [episode()], longest_closed: episode() },
    durability: {
      net: 124,
      trades: 20,
      without_top: [
        { fraction: 0.05, asked: 1, removed: 1, removed_net: 100, net: 24, sign_flips: false },
        { fraction: 0.1, asked: 2, removed: 1, removed_net: 150, net: -26, sign_flips: true },
      ],
    },
    stress: {
      historical: 60,
      reordered: null,
      grouped: null,
      sessions: 5,
      quantile: 0.95,
      resamples: 2000,
      block: 5,
      seed: 1,
      withheld: "5 sessions, fewer than 20",
    },
    coverage: { trades: 20, entry_time: 20, exit_time: 20, entry_spot: 18, exit_spot: 20 },
    ...over,
  };
}

describe("ArmCard", () => {
  it("shows a drawdown as a loss and the session runs beside the trade runs", () => {
    const html = text(<ArmCard module="flies" tag="control" a={arm()} updatedAt={0} />);
    expect(html).toContain("-$60.00");
    expect(html).toContain("22 W / 5 L");
    expect(html).toContain("1 down");
  });

  it("names a withheld stress test and draws its figures as dashes, never zero", () => {
    const html = text(<ArmCard module="flies" tag="control" a={arm()} updatedAt={0} />);
    expect(html).toContain("stress test withheld");
    expect(html).toContain("5 sessions, fewer than 20");
    expect(html).not.toContain("$0.00");
  });

  it("flags a sign flip and says when fewer winners were removed than asked", () => {
    const html = text(<ArmCard module="flies" tag="control" a={arm()} updatedAt={0} />);
    expect(html).toContain("sign flips");
    expect(html).toContain(" of 2");
  });

  it("says how many trades carry the spot fields when some do not", () => {
    const html = text(<ArmCard module="flies" tag="control" a={arm()} updatedAt={0} />);
    expect(html).toContain("18: entry spot");
  });

  it("marks an open drawdown as not yet recovered", () => {
    const open = episode({ recovered_session: null, open: true, sessions_recovering: null, days_recovering: null });
    const html = text(
      <ArmCard module="flies" tag="control" a={arm({ drawdowns: { count: 1, open, deepest: [open], longest_closed: null } })} updatedAt={0} />,
    );
    expect(html).toContain("not yet");
  });
});

function cap(over: Partial<CapitalReading> = {}): CapitalReading {
  return {
    peak_risk: {
      peak: 5525,
      session: "2026-09-04",
      at: null,
      trades: 37,
      capital_known: 37,
      untimed: 0,
      basis: "session",
      withheld: null,
    },
    return_on_peak_risk: -0.0348,
    max_drawdown_over_peak_risk: 0.3397,
    allocation: {
      declared: [],
      sessions: 30,
      covered: 0,
      uncovered: 0,
      return: null,
      max_drawdown: null,
      max_drawdown_dollars: null,
      current: null,
      capital_over_max_drawdown: null,
    },
    ...over,
  };
}

describe("CapitalTiles", () => {
  it("shows the observed peak and its ratios, and says when no allocation is declared", () => {
    const html = text(<CapitalTiles c={cap()} />);
    expect(html).toContain("$5525.00");
    expect(html).toContain("-3.5%");
    expect(html).toContain("34.0%");
    expect(html).toContain("none declared");
  });

  it("never names its net-over-largest-peak the performance slide's return on peak risk", () => {
    // Two different ratios: Σ net / Σ session peaks there, whole net / largest peak here.
    const html = text(<CapitalTiles c={cap()} />);
    expect(html).toContain("net / largest peak");
    expect(html.toLowerCase()).not.toContain(">return on peak risk<");
  });

  it("names a withheld peak and draws it as a dash, never zero", () => {
    const html = text(
      <CapitalTiles
        c={cap({
          peak_risk: { ...cap().peak_risk, peak: null, capital_known: 0, withheld: "no trade in this window records its capital at risk" },
          return_on_peak_risk: null,
          max_drawdown_over_peak_risk: null,
        })}
      />,
    );
    expect(html).toContain("peak risk unknown");
    expect(html).not.toContain("$0.00");
  });

  it("reads a declared allocation and counts the sessions before it", () => {
    const allocation = {
      ...cap().allocation,
      declared: [{ from: "2026-09-01", capital: 25000, note: null }],
      covered: 20,
      uncovered: 10,
      return: 0.012,
      max_drawdown: 0.08,
      max_drawdown_dollars: 2000,
      current: 25000,
      capital_over_max_drawdown: 12.5,
    };
    const html = text(<CapitalTiles c={cap({ allocation })} />);
    expect(html).toContain("$25000.00");
    expect(html).toContain("1.2%");
    expect(html).toContain("12.5×");
    expect(html).toContain("10 of 30 sessions come before the first declared allocation");
  });
});

function ladder(over: Partial<ExecutionReading> = {}): ExecutionReading {
  return {
    trades: 37,
    covered: 37,
    scope: "all",
    basis: "cost",
    net: 2088.04,
    slippage: 625,
    rows: [
      { multiple: 1, extra: 0, net: 2088.04, change: 0 },
      { multiple: 2, extra: -625, net: 1463.04, change: -0.2993 },
    ],
    breakeven_multiple: 4.34,
    withheld: null,
    ...over,
  };
}

describe("ExecutionTable", () => {
  it("shows the net at each multiple and where it reaches zero", () => {
    const html = text(<ExecutionTable e={ladder()} />);
    expect(html).toContain("$1463.04");
    expect(html).toContain("-29.9%");
    expect(html).toContain("reaches zero at 4.3×");
    expect(html).toContain("charged as its own cost");
  });

  it("says when it reads only the trades that record slippage, and that the net is theirs", () => {
    const html = text(<ExecutionTable e={ladder({ scope: "covered", covered: 137, trades: 301, basis: "in_gross" })} />);
    expect(html).toContain("137 of 301 trades that record slippage");
    expect(html).toContain("already inside gross");
  });

  it("names a withheld ladder and draws no table", () => {
    const html = text(<ExecutionTable e={ladder({ rows: [], withheld: "no trade in this window records its slippage" })} />);
    expect(html).toContain("not computed");
    expect(html).not.toContain("<table");
  });
});
