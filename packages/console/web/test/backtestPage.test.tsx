import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BacktestArmCards, BacktestOverview, BacktestRunCards, BacktestRunReport, BridgedResult } from "@console/shared";
import { BacktestDataTab, BacktestRunsTab, BacktestValidationTab } from "../src/pages/Backtest/BacktestPage";

/**
 * The Backtest page's three tabs, rendered with the bridge's documents already in the query cache
 * (a render that only ever sees the loading state is not a test). Pinned: the licence note heads
 * every tab; the unlock verdict and each check are the module's, shown as written; a run's money
 * is net, signed, whole-position dollars, with fees shown as a cost and never toned; a
 * `no_exposure` arm says so; the interval ends carry no tone; a failed read says why.
 */

const RUN_ID = "20261017-093000-a1b2c3";

const OVERVIEW: BacktestOverview = {
  ok: true,
  license: { active: true, terminated_on: null, purge_by: null, days_left: null, overdue: false },
  windows: { design: ["2023-01-03", "2025-06-30"], holdout: ["2025-07-01", "2026-06-30"] },
  stores: [
    {
      symbol: "SPX",
      cadence: 60,
      file: "SPX-60s.db",
      complete: 412,
      span: ["2024-10-01", "2026-06-30"],
      refused: 2,
      refused_reasons: { half_day: 2 },
    },
  ],
  validation: [
    {
      cadence: 60,
      file: "SPX-60s.json",
      dates: ["2026-10-13", "2026-10-14"],
      unlocks_history: false,
      sessions_compared: 2,
      vendor_at_least_recorder: { entries_match: true, settles_match: false },
      recorder: { measures: { sessions: 2 }, checks: { entries_match: true, settles_match: true } },
      vendor: { measures: { sessions: 2 }, checks: { entries_match: true, settles_match: false } },
      sessions: {
        "2026-10-13": {
          quotes: { ok: true, matched: 0.987, identical: 0.5, mid_gap: { median: 0, p90: 0.05, p99: 0.1, mean_signed: -0.001 }, spot_gap: null },
          profile: { ok: false, reason: "no vendor profile" },
        },
      },
    },
  ],
  runs: [
    {
      run_id: RUN_ID,
      created_at: 1_792_245_000,
      module: "flies",
      window: "validation",
      arms: ["control", "range-high"],
      span: ["2026-10-13", "2026-10-15"],
      cadence: 60,
      sessions: 3,
      refused: 1,
    },
  ],
  trials: { flies: 4 },
};

const REPORT: BacktestRunReport = {
  run_id: RUN_ID,
  window: "validation",
  span: ["2026-10-13", "2026-10-15"],
  cadence: 60,
  fill_model: "flies-paper-legged",
  code_commit: "abc1234",
  data_fingerprint: "fp-9f8e",
  trials_for_module: 4,
  refused: { "2026-10-15": "no_settle_price" },
  arms: {
    control: {
      spec_hash: "h1",
      summary: {
        sessions: 2,
        total: -120.5,
        mean: -60.25,
        sd: 10,
        sharpe_per_session: -6.025,
        psr: 0.01,
        trials: 1,
        dsr: null,
        min_track_record: null,
        mean_ci95_block_bootstrap: [-80, 40],
      },
      fees: 33.6,
      trades: 8,
    },
    "range-high": {
      spec_hash: "h2",
      summary: {
        sessions: 2,
        total: -120.5,
        mean: -60.25,
        sd: 10,
        sharpe_per_session: -6.025,
        psr: 0.01,
        trials: 1,
        dsr: null,
        min_track_record: null,
        mean_ci95_block_bootstrap: [-80, 40],
      },
      fees: 33.6,
      trades: 8,
      vs_control: { sessions: 2, mean_diff: 0, sd_diff: 0, total_diff: 0, ci: [0, 0] },
      no_exposure: true,
    },
  },
};

function render(node: React.ReactNode, url: string, seed: (c: QueryClient) => void): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(client);
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");
}

const seedOverview = (c: QueryClient, v: BridgedResult<BacktestOverview> = { ok: true, data: OVERVIEW, error: null }) =>
  c.setQueryData(["bridged", "backtest", "overview"], v);

describe("every tab", () => {
  it("opens with the licensed-data note", () => {
    for (const tab of [<BacktestDataTab />, <BacktestValidationTab />, <BacktestRunsTab />]) {
      const html = render(tab, "/backtest", (c) => seedOverview(c));
      expect(html).toContain("Licensed vendor data.");
      expect(html).toContain("do not screenshot, export, copy or share it");
    }
  });

  it("says why when the bridge failed, rather than drawing empty tables", () => {
    const html = render(<BacktestDataTab />, "/backtest", (c) =>
      seedOverview(c, { ok: false, data: null, error: "backtest read unavailable — exit 2" }),
    );
    expect(html).toContain("backtest read unavailable — exit 2");
    expect(html).not.toContain("vendor stores");
  });
});

describe("data", () => {
  it("shows the licence clock and each store with its refusals", () => {
    const html = render(<BacktestDataTab />, "/backtest/data", (c) => seedOverview(c));
    expect(html).toContain("active");
    expect(html).toContain("2023-01-03 → 2025-06-30");
    expect(html).toContain("SPX-60s.db");
    expect(html).toContain("412");
    expect(html).toContain("half_day 2");
  });
});

describe("validation", () => {
  it("states the module's unlock verdict and each check as written", () => {
    const html = render(<BacktestValidationTab />, "/backtest/validation", (c) => seedOverview(c));
    expect(html).toContain("does not unlock history");
    expect(html).toContain("settles_match");
    expect(html).toContain("98.7%");
    expect(html).toContain("no vendor profile");
  });
});

describe("runs", () => {
  it("lists runs, and reads the selected one's report", () => {
    const html = render(<BacktestRunsTab />, `/backtest/runs?run=${RUN_ID}`, (c) => {
      seedOverview(c);
      c.setQueryData(["bridged", "backtest", "run", RUN_ID], { ok: true, data: REPORT, error: null });
    });
    expect(html).toContain("flies 4");
    expect(html).toContain("row-selected");
    expect(html).toContain("flies-paper-legged");
    expect(html).toContain("abc1234");
    expect(html).toContain("fp-9f8e");
    expect(html).toContain("no_settle_price");
    expect(html).toContain("no exposure");
    // Net is signed and toned by its sign; fees are a cost, signed, and never toned.
    expect(html).toContain('<td class="pnl-neg">-$120.50</td>');
    expect(html).toContain("<td>-$33.60</td>");
    // Interval ends are never toned.
    expect(html).toContain("<td>-$80.00 to +$40.00</td>");
  });

  it("reads the run's cards: the account, the drawdowns, the calendar and how trades ended", () => {
    const arm: BacktestArmCards = {
      equity: [
        { session: "2024-01-02", net: 20, cum: 20, drawdown: 0 },
        { session: "2024-02-01", net: -50, cum: -30, drawdown: 50 },
      ],
      max_drawdown: 50,
      drawdowns: [
        {
          peak: "2024-01-02",
          peak_cum: 20,
          depth: 50,
          depth_pct: 0.119,
          trough: "2024-02-01",
          recovered: null,
          sessions_to_trough: 1,
          sessions_to_recover: null,
        },
      ],
      calendar: {
        months: { "2024-01": 20, "2024-02": -50 },
        years: [{ year: "2024", net: -30, sessions: 2, traded: 2, winning: 1, max_drawdown: 50 }],
      },
      sessions: { count: 2, wins: 1, losses: 1, win_rate: 0.5, avg_win: 20, avg_loss: -50, payoff_ratio: 0.4, best: 20, worst: -50 },
      trades: { count: 2, wins: 1, losses: 1, win_rate: 0.5, avg_win: 20, avg_loss: -50, payoff_ratio: 0.4, best: 20, worst: -50, not_recorded: 0 },
      exits: [
        { outcome: "expired_settlement", count: 1, share: 0.5, win_rate: 1, avg_net: 20, total_net: 20 },
        { outcome: "per_side_stop", count: 1, share: 0.5, win_rate: 0, avg_net: -50, total_net: -50 },
      ],
      edge_trend: null,
      peak_risk: 400,
      premium: { n: 2, premium: 200, fees: 2, fees_not_recorded: 0, fee_drag: 0.01, capture_rate: -0.15, capture_n: 2 },
      account: {
        capital: 400,
        ending: 370,
        return: -0.075,
        years: 0.08,
        annual_return: -0.9,
        max_drawdown: 0.119,
        return_to_drawdown: -7.56,
        ruined_on: null,
      },
    };
    const cards: BacktestRunCards = {
      ok: true,
      account: { capital: null, basis: "peak_risk" },
      arms: { control: arm, "range-high": { ...arm, account: null, exits: null, premium: null, peak_risk: null } },
    };
    const html = render(<BacktestRunsTab />, `/backtest/runs?run=${RUN_ID}`, (c) => {
      seedOverview(c);
      c.setQueryData(["bridged", "backtest", "run", RUN_ID], { ok: true, data: { ...REPORT, cards }, error: null });
    });
    expect(html).toContain("peak risk ($400)");
    expect(html).toContain("this arm&#x27;s own peak session risk");
    expect(html).toContain("1.0%"); // fee drag
    expect(html).toContain("-15.0%"); // capture rate
    expect(html).toContain("-7.5%"); // the return, a fraction shown as a percentage
    expect(html).toContain("11.9%"); // the drawdown as a share of peak equity
    expect(html).toContain("not recovered");
    expect(html).toContain("per_side_stop");
    expect(html).toContain("1 / 2"); // a year's winning sessions over those that traded
    expect(html).toContain("Too few sessions");
    expect(html).not.toContain("ruined");
    expect(html).toContain("+$20"); // a month cell, whole dollars

    const ruined = { ...cards, arms: { control: { ...arm, account: { ...arm.account!, ruined_on: "2024-02-01" } } } };
    const html2 = render(<BacktestRunsTab />, `/backtest/runs?run=${RUN_ID}`, (c) => {
      seedOverview(c);
      c.setQueryData(["bridged", "backtest", "run", RUN_ID], { ok: true, data: { ...REPORT, cards: ruined }, error: null });
    });
    expect(html2).toContain("ruined 2024-02-01");
    expect(html2).toContain("an account this size would have stopped there");
  });

  it("a chosen account is read from the URL and keys its own query", () => {
    const html = render(<BacktestRunsTab />, `/backtest/runs?run=${RUN_ID}&capital=50000`, (c) => {
      seedOverview(c);
      c.setQueryData(["bridged", "backtest", "run", RUN_ID, 50000], {
        ok: true,
        data: { ...REPORT, cards: { ok: false, error: "no session results" } },
        error: null,
      });
    });
    expect(html).toContain('aria-pressed="true">$50,000');
    expect(html).toContain("Cards unavailable: no session results");
  });

  it("a run the module does not know is its error, not an empty report", () => {
    const html = render(<BacktestRunsTab />, `/backtest/runs?run=${RUN_ID}`, (c) => {
      seedOverview(c);
      c.setQueryData(["bridged", "backtest", "run", RUN_ID], { ok: false, data: null, error: `no run '${RUN_ID}'` });
    });
    expect(html).toContain(`no run &#x27;${RUN_ID}&#x27;`);
  });
});
