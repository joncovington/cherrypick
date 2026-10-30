/**
 * What history says about each module's strategy, in words, for the status line at the head of its
 * summary (docs/console-review-plan.md, batch 1). Verdict words and where they are written down,
 * never a licensed figure. A module missing here shows no tested line rather than a guess.
 */
export interface TestedStatus {
  /** One or two plain sentences: whether the strategy has a tested edge. */
  words: string;
  /** Where the verdict is written, as a repo path. */
  source: string;
}

export const TESTED_STATUS: Readonly<Record<string, TestedStatus>> = {
  meic: {
    words:
      "No tested edge yet: the backtest is not validated for MEIC, and the delta and entry-slot studies (items 23 and 24) found nothing better than the live rule.",
    source: "docs/edge-hypotheses-plan.md",
  },
  bwb: {
    words:
      "History finds the trade negative after costs on every arm; the entry gate halves drawdown but does not make it pay. Live runs to measure fills, not for edge.",
    source: "docs/history-studies-2026-10.md (a)",
  },
  earnings: {
    words: "Each strategy's tested status is on the strategies tab.",
    source: "packages/earnings/docs/05-strategies.md",
  },
  pmcc: {
    words:
      "No tested edge in the short calls: their edge in history rests on the implied-vol assumption, and the roll policy and the dynamic short delta were both studied and closed.",
    source: "packages/pmcc/docs/shield-study.md",
  },
  calendars: {
    words: "Not tested for edge: the replay matches the paper ledger to the cent, but no study has asked whether the double calendar pays.",
    source: "docs/history-studies-2026-10-b.md (6)",
  },
  curve: {
    words: "The VXX spread's edge is real in replay, but costs take about half of each credit and the market is about ten contracts deep.",
    source: "packages/contango/CLAUDE.md",
  },
  contango: {
    words: "In replay (2018–26) the gate buys drawdown, not return: holding SVXY outright earned more.",
    source: "packages/contango/CLAUDE.md",
  },
};
