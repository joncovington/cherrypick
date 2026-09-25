import { useCurve } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Curve/IntegrityStrip";
import { BookComparison, RegimeCard, OpenTradesCard } from "../../pages/Curve/CurrentStateCards";
import { CurveSession } from "../../pages/Curve/CurveSession";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HistoryTab } from "../../pages/Curve/HistoryTab";
import { HelpTab } from "../../pages/Curve/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { CURVE_SLIDES, type CurveSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const CURVE_LABEL = Object.fromEntries(CURVE_SLIDES.map((s) => [s.id, s.label])) as Record<CurveSlideId, string>;

/**
 * curve (VXX term-structure roll-yield harvest), on the module frame since 2026-09-25: a left rail
 * of pages, and nothing on the surface opens an overlay.
 */
export function CurveLightbox({ slide }: { slide: string }) {
  const { data, isLoading, dataUpdatedAt } = useCurve();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";
  const todayRegime = data?.regimeSeries.find((r) => r.tradeDate === data.session);

  const slides: Array<SlideDef & { id: CurveSlideId }> = [
    { id: "session", label: CURVE_LABEL.session, render: () => <CurveSession data={data} loading={isLoading} /> },
    {
      id: "regime",
      label: CURVE_LABEL.regime,
      render: () => (
        <div className="cards cards-wide">
          <RegimeCard series={data?.regimeSeries ?? []} today={todayRegime} updatedAt={dataUpdatedAt} />
        </div>
      ),
    },
    {
      id: "decisions",
      label: CURVE_LABEL.decisions,
      render: () => (
        <div className="cards cards-wide">
          <DecisionsCard module="curve" />
        </div>
      ),
    },
    {
      id: "arms",
      label: CURVE_LABEL.arms,
      render: () => (
        <div className="cards cards-wide">
          <BookComparison data={data} flipDivergence={data?.flipDivergence} updatedAt={dataUpdatedAt} />
        </div>
      ),
    },
    { id: "performance", label: CURVE_LABEL.performance, render: () => <PerformanceSlide module="curve" /> },
    { id: "advisor", label: CURVE_LABEL.advisor, render: () => <AdvisorSlide module="curve" /> },
    {
      id: "positions",
      label: CURVE_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">{isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}</div>
      ),
    },
    { id: "history", label: CURVE_LABEL.history, render: () => <HistoryTab /> },
    { id: "guide", label: CURVE_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
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
