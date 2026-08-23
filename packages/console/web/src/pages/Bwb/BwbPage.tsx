import { useState } from "react";
import { useBwb } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card } from "../../components/DataTable";
import { LoopPill, TabStrip } from "../../components/ScopeBar";
import { IntegrityStrip } from "./IntegrityStrip";
import { BookComparison, FireCountsCard, SymbolCards } from "./CurrentStateCards";
import { HistoryTab } from "./HistoryTab";
import { HelpTab } from "./HelpTab";

type BwbTab = "today" | "history" | "help";

const TABS = ["today", "history", "help"] as const;

/**
 * bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment).
 *
 * No mode toggle -- structural, not a default: the module has no live loop and no live store. Its
 * `live.enabled` config field is a documented placeholder (packages/bwb/CLAUDE.md).
 *
 * Only the "today" tab content gates on `!data.dbPresent` -- history and help render unconditionally
 * regardless of whether the module has ever run on this machine, the 2026-08-22 tab-gating fix
 * landed here from the start (curve/pmcc shipped this bug first and fixed it after the fact).
 */
export function BwbPage() {
  const [tab, setTab] = useState<BwbTab>("today");
  const { data, isLoading, isError, dataUpdatedAt } = useBwb();

  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>bwb</h1>
        <PaperLiveBadge mode="paper" />
        <TabStrip tabs={TABS} value={tab} onChange={setTab} ariaLabel="bwb tabs" />
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
            <Card title="bwb" collapseKey="bwb-absent">
              <p className="muted">
                This module has not run on this machine -- there is no paper store at{" "}
                <span className="mono">~/.cherrypick/data/bwb/paper_trades.db</span> yet. bwb was built
                2026-08-23; the page fills in once its first scheduled session runs.
              </p>
            </Card>
          </div>
        ) : (
          <div className="cards cards-wide">
            <IntegrityStrip data={data} updatedAt={dataUpdatedAt} />

            {isLoading ? null : <SymbolCards data={data} updatedAt={dataUpdatedAt} />}

            <FireCountsCard
              counts={data?.fireCounts ?? []}
              correlationCaveat={
                data?.correlationCaveat ??
                "concurrent positions share regime context -- rows are not independent samples"
              }
              updatedAt={dataUpdatedAt}
            />

            <BookComparison data={data} updatedAt={dataUpdatedAt} />
          </div>
        ))}

      {tab === "history" && <HistoryTab />}
      {tab === "help" && <HelpTab data={data} />}
      {isError && <p className="muted">could not reach the bwb endpoint</p>}
    </div>
  );
}
