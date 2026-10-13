import type { ReactNode } from "react";
import type { CompareArm } from "@console/shared";
import { fmtCash, fmtCount, fmtMoney } from "../../lib/format";

/**
 * The arm table's columns, declared once: header, the definition its title states, the sort key,
 * and the cell. Every figure is the bridge's (`core.compare` over `core.stats`); nothing here
 * computes a statistic or compares one to a threshold.
 *
 * **Tone comes only from a number's sign** (gross, net, the per-session means): the interval
 * endpoints, the t bound, the sign-test p and the projection are never toned, because toning them
 * by sign or size would be a significance verdict painted on by the console.
 */

export interface CompareColumn {
  id: string;
  header: string;
  title: string;
  /** Sort accessor; absent = not sortable. Nulls always sink (`sortRows`). */
  sort?: (a: CompareArm) => number | string | null;
  render: (a: CompareArm) => ReactNode;
  /** Right-aligned figure. */
  num?: boolean;
  group?: "vs";
}

/** Signed dollars, toned by sign; a null is an em dash with no tone. */
export function SignedCash({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  return <span className={v > 0 ? "pnl-pos" : v < 0 ? "pnl-neg" : undefined}>{fmtCash(v)}</span>;
}

/** An interval, untoned: `[-$464.49, +$11.48]`. */
export function fmtInterval(ci: [number, number] | null | undefined): string {
  return ci === null || ci === undefined ? "—" : `[${fmtCash(ci[0])}, ${fmtCash(ci[1])}]`;
}

const dash = (v: number | null | undefined, f: (n: number) => string): string =>
  v === null || v === undefined ? "—" : f(v);

export function compareColumns(costsNote: string, levelPct: string): CompareColumn[] {
  return [
    {
      id: "control",
      header: "control",
      title: "the arm this one is paired against: control in the module, an advised book's experiment base, <prefix>:control for a <prefix>:<arm>",
      sort: (a) => a.control,
      render: (a) => (a.control === null ? <span className="muted">—</span> : a.control),
    },
    { id: "trades", header: "trades", title: "closed trades in scope", sort: (a) => a.trades, render: (a) => fmtCount(a.trades), num: true },
    { id: "sessions", header: "sessions", title: "sessions with at least one closed trade in scope", sort: (a) => a.sessions, render: (a) => fmtCount(a.sessions), num: true },
    {
      id: "gross",
      header: "gross",
      title: "sum of every closed trade's gross P&L in scope, whole-position dollars, before any cost",
      sort: (a) => a.gross,
      render: (a) => <SignedCash v={a.gross} />,
      num: true,
    },
    {
      id: "costs",
      header: "costs",
      title: `${costsNote} Shown as the dollars subtracted.`,
      sort: (a) => a.costs,
      render: (a) => <span className="muted">{fmtMoney(a.costs)}</span>,
      num: true,
    },
    { id: "net", header: "net", title: "gross − costs: the P&L, whole-position dollars", sort: (a) => a.net, render: (a) => <SignedCash v={a.net} />, num: true },
    {
      id: "win",
      header: "win rate",
      title: "share of closed trades whose net was above zero",
      sort: (a) => a.win_rate,
      render: (a) => dash(a.win_rate, (v) => `${(v * 100).toFixed(1)}%`),
      num: true,
    },
    {
      id: "dd",
      header: "max dd",
      title: "maximum drawdown: the largest peak-to-trough fall of the per-SESSION net equity path, in dollars (sessions, not trades)",
      sort: (a) => a.max_drawdown,
      render: (a) => <span className="muted">{fmtMoney(a.max_drawdown)}</span>,
      num: true,
    },
    {
      id: "mean",
      header: "mean / session",
      title: `mean net per session, with its ${levelPct} session-bootstrap interval beneath`,
      sort: (a) => a.mean_session_net,
      render: (a) => (
        <>
          <SignedCash v={a.mean_session_net} />
          <div className="muted cmp-sub">{fmtInterval(a.session_net_ci)}</div>
        </>
      ),
      num: true,
    },
    {
      id: "more",
      header: "sessions until",
      title: "further sessions until the arm's own interval would exclude zero, IF the observed mean and spread held — a projection, not a forecast; 0 when it already does",
      sort: (a) => a.sessions_to_exclude_zero,
      render: (a) => fmtCount(a.sessions_to_exclude_zero),
      num: true,
    },
    {
      id: "vs_sessions",
      header: "paired",
      title: "sessions both this arm and its control closed, inside the overlap of their windows (multi-day structures pair on the entry session)",
      sort: (a) => a.vs_control?.sessions ?? null,
      render: (a) => fmtCount(a.vs_control?.sessions ?? null),
      num: true,
      group: "vs",
    },
    {
      id: "vs_mean",
      header: "mean diff",
      title: `mean of (arm − control) net per paired session, dollars, with its ${levelPct} session-bootstrap interval beneath`,
      sort: (a) => a.vs_control?.mean_diff ?? null,
      render: (a) => (
        <>
          <SignedCash v={a.vs_control?.mean_diff} />
          <div className="muted cmp-sub">{fmtInterval(a.vs_control?.ci)}</div>
        </>
      ),
      num: true,
      group: "vs",
    },
    {
      id: "vs_t",
      header: "t lower",
      title: "one-sided lower confidence bound on the mean difference (Student t, at the bridge's alpha), dollars",
      sort: (a) => a.vs_control?.t_lower ?? null,
      render: (a) => fmtCash(a.vs_control?.t_lower ?? null),
      num: true,
      group: "vs",
    },
    {
      id: "vs_p",
      header: "sign test",
      title: "exact two-sided binomial sign test p over the paired sessions, ties dropped; beneath, sessions the arm did better / the control did better",
      sort: (a) => a.vs_control?.sign_test_p ?? null,
      render: (a) => {
        const vs = a.vs_control;
        if (vs === null) return <span className="muted">—</span>;
        return (
          <>
            {dash(vs.sign_test_p, (p) => `p ${p.toFixed(4)}`)}
            <div className="muted cmp-sub">
              {vs.arm_better} / {vs.control_better}
              {vs.ties > 0 ? ` · ${String(vs.ties)} tied` : ""}
            </div>
          </>
        );
      },
      num: true,
      group: "vs",
    },
    {
      id: "vs_more",
      header: "sessions until",
      title: "further paired sessions until the difference's interval would exclude zero, IF the observed mean and spread held — a projection, not a forecast; 0 when it already does",
      sort: (a) => a.vs_control?.sessions_to_exclude_zero ?? null,
      render: (a) => fmtCount(a.vs_control?.sessions_to_exclude_zero ?? null),
      num: true,
      group: "vs",
    },
    {
      id: "vs_unpaired",
      header: "arm / ctl only",
      title: "sessions only the arm closed / only the control closed: counted, never filled with zero, and not in the paired figures",
      sort: (a) => (a.vs_control === null ? null : a.vs_control.arm_only_sessions + a.vs_control.control_only_sessions),
      render: (a) =>
        a.vs_control === null ? <span className="muted">—</span> : `${String(a.vs_control.arm_only_sessions)} / ${String(a.vs_control.control_only_sessions)}`,
      num: true,
      group: "vs",
    },
  ];
}
