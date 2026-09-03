import { useBwb } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card } from "../../components/DataTable";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Bwb/IntegrityStrip";
import { BookComparison, FireCountsCard, OpenTradesCard } from "../../pages/Bwb/CurrentStateCards";
import { HistoryTab } from "../../pages/Bwb/HistoryTab";
import { HelpTab } from "../../pages/Bwb/HelpTab";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/** bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment). */
export function BwbLightbox({ slide }: { slide: string }) {
  const { data, isLoading, dataUpdatedAt } = useBwb();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: SlideDef[] = [
    {
      id: "now",
      label: "now",
      render: () =>
        data !== undefined && !data.dbPresent ? (
          <div className="cards cards-wide">
            <Card title="bwb" collapseKey="bwb-absent">
              <p className="muted">
                This module has not run on this machine -- there is no paper store at{" "}
                <span className="mono">~/.cherrypick/data/bwb/paper_trades.db</span> yet. bwb was
                built 2026-08-23; the page fills in once its first scheduled session runs.
              </p>
            </Card>
          </div>
        ) : (
          <div className="cards cards-wide">
            <IntegrityStrip data={data} updatedAt={dataUpdatedAt} />
            {isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}
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
        ),
    },
    { id: "history", label: "history", render: () => <HistoryTab /> },
    { id: "guide", label: "help", render: () => <HelpTab data={data} /> },
  ];

  return (
    <LightboxFrame
      module="bwb"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode="paper" />}
      loopPill={
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={data?.today.lastIteration?.ageSeconds ?? null}
          detail={
            data?.today.lastIteration == null
              ? "no loop iterations recorded"
              : `${data.today.lastIteration.phase} · ${data.today.lastIteration.status}`
          }
        />
      }
      session={data?.session ?? null}
    />
  );
}
