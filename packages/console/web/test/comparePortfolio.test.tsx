import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { portfolioArmLabel, type PnlDay, type PortfolioReading, type PortfolioSource } from "@console/shared";
import { PortfolioView, armsByModule, backtestOptions, defaultArm } from "../src/pages/Compare/PortfolioTab";

const html = (p: PortfolioReading) => renderToString(<PortfolioView p={p} />).replace(/<!--\s*-->/g, "");

describe("the arm picker", () => {
  it("lists each module's arms and defaults to control, else its first arm that is no experiment", () => {
    const day = (module: PnlDay["module"], tag: string): PnlDay => ({ module, tag, session: "2026-10-16", net: 1 });
    const m = armsByModule([day("flies", "range-high"), day("flies", "control"), day("earnings", "advised:x@e1"), day("earnings", "strat_test:iron_fly"), day("meic", "control")], (mod) => mod !== "meic");
    expect([...m.keys()]).toEqual(["earnings", "flies"]);
    expect(defaultArm(m.get("flies") ?? [])).toBe("control");
    expect(defaultArm(m.get("earnings") ?? [])).toBe("strat_test:iron_fly");
    expect(defaultArm(["advised:x"])).toBeNull();
  });

  it("offers each Builder automation's newest run and every study arm, labelled as backtests", () => {
    const src = (id: string, kind: PortfolioSource["kind"], arms: string[], at: string | null, licensed = false): PortfolioSource => ({
      id, kind, label: kind === "study" ? "item 75 study-all" : id, arms, window: ["2025-11-17", "2026-11-16"], licensed, generated_at: at,
    });  // fmt: skip
    const opts = backtestOptions([
      src("automations/builder-mine-2611161730", "builder", ["mine"], "2026-11-16T17:30:00"),
      src("automations/builder-mine-2611171730", "builder", ["mine"], "2026-11-17T17:30:00"),
      src("results/research/item75/study-all", "study", ["iron-condor-1345", "long-put-1030"], null, true),
    ]);
    expect(opts.map((o) => o.key)).toEqual([
      "bt:results/research/item75/study-all#iron-condor-1345",
      "bt:results/research/item75/study-all#long-put-1030",
      "bt:automations/builder-mine-2611171730#mine",
    ]);
    expect(opts.find((o) => o.key.endsWith("#mine"))?.label).toBe("mine (Builder run, 2025-11-17 → 2026-11-16)");
    expect(opts[0]?.licensed).toBe(true);
    expect(portfolioArmLabel("bt:results/research/item75/study-all#iron-condor-1345")).toBe("iron-condor-1345 (backtest)");
    expect(portfolioArmLabel("flies:control")).toBe("flies:control");
  });
});

const reading: PortfolioReading = {
  ok: true,
  period: "day",
  weights: "equal_risk",
  window: { start: "2026-08-21", end: "2026-10-16", periods: 40, from: "2026-08-21", combined_periods: 40 },
  shared: { from: "2026-08-31", periods: 34, thin: false, arms: ["flies:control", "meic:control"], too_new: ["contango:control"] },
  min_periods: 20,
  arms: [
    { key: "flies:control", periods_traded: 38, first: "2026-08-21", periods_in_span: 40, net: 1545.88, max_drawdown: 3379.47, sd: 500, scale: 0.998 },
    { key: "meic:control", periods_traded: 38, first: "2026-08-21", periods_in_span: 40, net: 815241.1, max_drawdown: 91045.75, sd: 35000, scale: 0.0143 },
    { key: "pmcc:shield", periods_traded: 0, first: null, periods_in_span: 0, net: 0, max_drawdown: 0, sd: null, scale: null },
  ],
  no_closes: ["pmcc:shield"],
  correlation: [{ a: "flies:control", b: "meic:control", zero_filled: 0.4791, zero_filled_periods: 40, similarity: 0.4971, similarity_periods: 37, thin: false }],
  effective_arms: 2.71,
  drawdown: { combined: 6694.72, sum_of_arms: 11837.71, benefit: 0.4345, by_arm: {}, episode: { from: "2026-10-13", to: "2026-10-16" } },
  worst_periods: [{ period: "2026-10-16", net: -5730.62, arms_lost: 4, arms_traded: 4, by_arm: { "flies:control": -800 } }],
  contribution: [
    { key: "flies:control", net: 1542.74, share: 0.1395, in_worst_drawdown: -2322.12 },
    { key: "meic:control", net: 11675.39, share: 1.0554, in_worst_drawdown: -823.42 },
  ],
  combined_net: 11062.0,
  failures: [],
};

describe("PortfolioView", () => {
  it("shows effective arms over the shared window, the drawdown saving, and each arm's scale", () => {
    const out = html(reading);
    for (const s of ["2.71 of 2", "34 sessions", "combining saves", "43%", "×0.0143", "flies:control", "0.48", "0.50", "4 / 4"]) expect(out).toContain(s);
  });

  it("names an arm too new to count in effective arms", () => {
    expect(html(reading)).toContain("too new to count in effective arms");
    expect(html(reading)).toContain("contango:control");
  });

  it("names an arm with no closes rather than zero-filling it", () => {
    expect(html(reading)).toContain("no closes in the window, so nothing to hold together: pmcc:shield");
  });

  it("marks a correlation thin and drops its tone under min_periods", () => {
    const thin = { ...reading, correlation: [{ ...reading.correlation[0]!, zero_filled: 0.9, zero_filled_periods: 8, similarity_periods: 8, thin: true }] };
    const out = html(thin);
    expect(out).toContain("thin");
    expect(out).not.toContain('class="pnl-neg">0.90');
  });

  it("shows the risk held at once and names the arms it could not measure", () => {
    const open_risk = {
      sessions: 3, peaks: { median: 900, p95: 1500, max: 1500 }, account: null, of_account: null,
      worst_sessions: [{ session: "2026-10-14", peak: 1500, by_arm: { "meic:control": 1000, "flies:control": 500 } }],
      measured: { "meic:control": 12 }, unmeasured: { "flies:control": 4 }, whole_session: {}, floor: true,
    };  // fmt: skip
    const out = html({ ...reading, open_risk });
    expect(out).toContain("risk held at once");
    expect(out).toContain("a floor");
    expect(out).toContain("flies:control (4)");
    expect(out).toContain("2026-10-14");
    const none = html({ ...reading, open_risk: { ...open_risk, unmeasured: {}, floor: false } });
    expect(none).not.toContain("a floor");
  });

  it("says when it holds a licensed backtest, and labels a backtest arm", () => {
    const key = "bt:results/research/item75/study-all#iron-condor-1345";
    const arm = { ...reading.arms[0]!, key, span_from: "2022-05-16" };
    const out = html({ ...reading, arms: [arm], contribution: [], licensed: true });
    expect(out).toContain("this reading stays on this machine");
    expect(out).toContain("iron-condor-1345 (backtest)");
    expect(out).toContain("2022-05-16");
    expect(html(reading)).not.toContain("this reading stays on this machine");
  });
});
