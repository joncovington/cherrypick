import { useCurve } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card } from "../../components/DataTable";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Curve/IntegrityStrip";
import { BookComparison, RegimeCard, OpenTradesCard } from "../../pages/Curve/CurrentStateCards";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HistoryTab } from "../../pages/Curve/HistoryTab";
import { HelpTab } from "../../pages/Curve/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/** curve (VXX term-structure roll-yield harvest). */
export function CurveLightbox({ slide }: { slide: string }) {
  const { data, isLoading, dataUpdatedAt } = useCurve();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";
  const todayRegime = data?.regimeSeries.find((r) => r.tradeDate === data.session);

  const slides: SlideDef[] = [
    {
      id: "now",
      label: "now",
      render: () =>
        data !== undefined && !data.dbPresent ? (
          <div className="cards cards-wide">
            <Card title="curve" collapseKey="curve-absent">
              <p className="muted">
                This module has not run on this machine -- there is no paper store at{" "}
                <span className="mono">~/.cherrypick/data/curve/paper_trades.db</span> yet. curve
                was built 2026-08-22; the page fills in once its first scheduled session runs.
              </p>
            </Card>
          </div>
        ) : (
          <div className="cards cards-wide">
            <RegimeCard series={data?.regimeSeries ?? []} today={todayRegime} updatedAt={dataUpdatedAt} />
            {isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}
            <DecisionsCard module="curve" />
            <BookComparison data={data} flipDivergence={data?.flipDivergence} updatedAt={dataUpdatedAt} />
          </div>
        ),
    },
    { id: "history", label: "history", render: () => <HistoryTab /> },
    { id: "performance", label: "performance", render: () => <PerformanceSlide module="curve" /> },
    { id: "guide", label: "help", render: () => <HelpTab data={data} /> },
  ];

  return (
    <LightboxFrame
      module="curve"
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
      integrity={<IntegrityStrip data={data} updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity.measurementBreaks.length ?? 0) > 0 || (data?.integrity.schemaDrift.length ?? 0) > 0}
    />
  );
}
