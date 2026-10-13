import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BridgedResult, FliesGateSessions, FliesLiveGap, FliesSelectorSessions } from "@console/shared";
import { LiveGapSlide } from "../src/pages/Flies/LiveGapSlide";
import { GatesSlide } from "../src/pages/Flies/GatesSlide";
import { SelectorSlide } from "../src/pages/Flies/SelectorSlide";

/**
 * The three flies evidence slides render the module's numbers as given (bridged, never derived),
 * show a failed read as that failure rather than an empty table, and keep the completion-rule break
 * visible between the rows it separates.
 */

function render(key: string, value: BridgedResult<unknown>, el: ReactElement): string {
  const client = new QueryClient();
  client.setQueryData(["bridged", key], value);
  return renderToString(<QueryClientProvider client={client}>{el}</QueryClientProvider>);
}

const side = (entries: number, completed: number, net: number | null) => ({
  entries,
  completed,
  completion_rate: entries ? completed / entries : null,
  settled: entries,
  open: 0,
  net,
  fees: 10,
});

const session = (day: string, rule: "modelled_debit" | "pays_limit", netGap: number) => ({
  session: day,
  completion_rule: rule,
  live: side(2, 1, 100),
  paper: side(4, 3, 100 - netGap),
  net_gap: netGap,
  completion_gap: 0.25,
  matched: 1,
  unmatched_live: 1,
  unmatched_paper: 3,
  credit_gap_sum: 0.15,
  debit_gap_sum: -0.05,
  debit_pairs: 1,
  fee_gap: 1.23,
  matched_net_gap: 7.77,
  broker_fees: 1,
});

const gap: FliesLiveGap = {
  ok: true,
  ledger: "ok",
  arm: "control",
  match_window_minutes: 30,
  completion_rule_from: "2026-10-05",
  sessions: [session("2026-10-02", "modelled_debit", 333.33), session("2026-10-06", "pays_limit", -44.44)],
  eras: {},
};

describe("the live vs paper slide", () => {
  it("renders the module's gaps and marks the completion-rule break between its rows", () => {
    const html = render("flies-live-gap", { ok: true, data: gap, error: null }, <LiveGapSlide />);
    expect(html).toContain("$333.33");
    expect(html).toContain("-$44.44");
    expect(html).toContain("+0.15");
    expect(html).toContain("measurement break 2026-10-05 (completion_rule)");
    expect(html).toContain("2026-10-05");
  });

  it("shows a failed read as the failure", () => {
    const html = render("flies-live-gap", { ok: false, data: null, error: "flies evidence unavailable — exit 1" }, <LiveGapSlide />);
    expect(html).toContain("flies evidence unavailable");
    expect(html).not.toContain("per session");
  });
});

const gates: FliesGateSessions = {
  ok: true,
  ledger: "ok",
  as_of: "2026-10-12",
  era_start: "2026-10-05",
  era_break: { break_date: "2026-10-05", kind: "completion_rule", reason: "x" },
  arms: [
    {
      arm: "vol-floor",
      base: "control",
      window_start: "2026-10-05",
      window_break: { break_date: "2026-10-05", scope: "vol-floor", kind: "arm_added", reason: "x" },
      acts_by: ["straddle_below_floor"],
      acted: { sessions: 1, scored: 1, arm_net: -10, control_net: 20, diff: -30, arm_ahead: 0, arm_behind: 1 },
      quiet: { sessions: 0, scored: 0, arm_net: null, control_net: null, diff: null, arm_ahead: 0, arm_behind: 0 },
      all: { sessions: 1, scored: 1, arm_net: -10, control_net: 20, diff: -30, arm_ahead: 0, arm_behind: 1 },
      sessions: [
        {
          session: "2026-10-08",
          acted: true,
          refused_ticks: 737,
          refusals: { straddle_below_floor: 737 },
          close_tags: 0,
          restacks: 0,
          arm_entries: 2,
          control_entries: 5,
          arm_net: -10,
          control_net: 20,
          diff: -30,
          partial_session: true,
        },
      ],
    },
  ],
};

describe("the gates slide", () => {
  it("renders each arm's own window, what acted and the module's diff", () => {
    const html = render("flies-gates", { ok: true, data: gates, error: null }, <GatesSlide />);
    expect(html).toContain("vol-floor vs control");
    expect(html).toContain("straddle_below_floor ×737");
    expect(html).toContain("-$30.00");
    expect(html).toContain("partial");
  });
});

const selector: FliesSelectorSessions = {
  ok: true,
  ledger: "ok",
  procedure_version: 2,
  features: ["vol_bucket", "trend_bucket"],
  latest_model: {
    session: "2026-10-13",
    model_id: null,
    fitted_through: "2026-10-12",
    procedure_version: 1,
    arm_starts: null,
    ok: false,
    reason: "model_wrong_procedure",
    cells: [],
  },
  sessions: [],
  departing: { sessions: 0, first_read_at: 10, due: false, ahead: 0, behind: 0, diff: null, first_read: null },
};

describe("the selector slide", () => {
  it("says the selector has not traded and names why the newest model would be refused", () => {
    const html = render("flies-selector", { ok: true, data: selector, error: null }, <SelectorSlide />);
    expect(html).toContain("has not traded yet");
    expect(html).toContain("model_wrong_procedure");
    expect(html).toContain("procedure v<!-- -->1");
  });
});
