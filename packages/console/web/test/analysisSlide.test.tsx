import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ArmAnalysis, DrawdownEpisode } from "@console/shared";
import { ArmCard } from "../src/components/analysis/AnalysisSlide";

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
