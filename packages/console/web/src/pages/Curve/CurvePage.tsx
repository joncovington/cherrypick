import { useState } from "react";
import { useCurve } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card } from "../../components/DataTable";
import { LoopPill, TabStrip } from "../../components/ScopeBar";
import { IntegrityStrip } from "./IntegrityStrip";
import { BookComparison, RegimeCard, OpenTradesCard } from "./CurrentStateCards";
import { HistoryTab } from "./HistoryTab";
import { HelpTab } from "./HelpTab";

type CurveTab = "today" | "history" | "help";

const TABS = ["today", "history", "help"] as const;

/**
 * curve (VXX term-structure roll-yield harvest).
 *
 * No mode toggle -- structural, not a default: the module has no live loop and no live store. Its
 * `live.enabled` config field is a documented placeholder (packages/curve/CLAUDE.md).
 *
 * The measurement-integrity strip leads, ahead of any P&L, for the same reason pmcc's does: the
 * paper net is an upper bound while early assignment sits unmodelled, and a caveat met after the
 * number has already been read is a caveat that arrived too late. The regime card sits beside it
 * because the module states plainly that series is its OWN second product, not merely context for
 * a position -- it has value on a day nothing traded at all.
 */
export function CurvePage() {
  const [tab, setTab] = useState<CurveTab>("today");
  const { data, isLoading, isError, dataUpdatedAt } = useCurve();

  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const todayRegime = data?.regimeSeries.find((r) => r.tradeDate === data.session);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>curve</h1>
        <PaperLiveBadge mode="paper" />
        <TabStrip tabs={TABS} value={tab} onChange={setTab} ariaLabel="curve tabs" />
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={data?.today.lastIteration?.ageSeconds ?? null}
          detail={
            data?.today.lastIteration == null
              ? "no loop iterations recorded"
              : `${data.today.lastIteration.phase} · ${data.today.lastIteration.status}`
          }
        />
        {data?.session != null && <span className="muted">session {data.session}</span>}
      </div>

      {tab === "today" &&
        (data !== undefined && !data.dbPresent ? (
          <div className="cards cards-wide">
            <Card title="curve" collapseKey="curve-absent">
              <p className="muted">
                This module has not run on this machine -- there is no paper store at{" "}
                <span className="mono">~/.cherrypick/data/curve/paper_trades.db</span> yet. curve was built
                2026-08-22; the page fills in once its first scheduled session runs.
              </p>
            </Card>
          </div>
        ) : (
          <div className="cards cards-wide">
            <IntegrityStrip data={data} updatedAt={dataUpdatedAt} />

            <RegimeCard series={data?.regimeSeries ?? []} today={todayRegime} updatedAt={dataUpdatedAt} />

            {isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}

            <BookComparison data={data} flipDivergence={data?.flipDivergence} updatedAt={dataUpdatedAt} />
          </div>
        ))}

      {tab === "history" && <HistoryTab />}
      {tab === "help" && <HelpTab data={data} />}
      {isError && <p className="muted">could not reach the curve endpoint</p>}
    </div>
  );
}
