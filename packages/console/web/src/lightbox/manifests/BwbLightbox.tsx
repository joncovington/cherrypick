import { useBwb } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Bwb/IntegrityStrip";
import { BookComparison, FireCountsCard, OpenTradesCard } from "../../pages/Bwb/CurrentStateCards";
import { BwbSession } from "../../pages/Bwb/BwbSession";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HistoryTab } from "../../pages/Bwb/HistoryTab";
import { HelpTab } from "../../pages/Bwb/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { BWB_SLIDES, type BwbSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const BWB_LABEL = Object.fromEntries(BWB_SLIDES.map((s) => [s.id, s.label])) as Record<BwbSlideId, string>;

/**
 * bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment), on the
 * module frame since 2026-09-25: a left rail of pages, and nothing on the surface opens an overlay.
 */
export function BwbLightbox({ slide }: { slide: string }) {
  const { data, isLoading, dataUpdatedAt } = useBwb();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: BwbSlideId }> = [
    { id: "session", label: BWB_LABEL.session, render: () => <BwbSession data={data} loading={isLoading} /> },
    {
      id: "decisions",
      label: BWB_LABEL.decisions,
      render: () => (
        <div className="cards cards-wide">
          <DecisionsCard module="bwb" />
        </div>
      ),
    },
    {
      id: "arms",
      label: BWB_LABEL.arms,
      render: () => (
        <div className="cards cards-wide">
          <BookComparison data={data} updatedAt={dataUpdatedAt} />
          <FireCountsCard
            counts={data?.fireCounts ?? []}
            correlationCaveat={
              data?.correlationCaveat ?? "concurrent positions share regime context -- rows are not independent samples"
            }
            updatedAt={dataUpdatedAt}
          />
        </div>
      ),
    },
    { id: "performance", label: BWB_LABEL.performance, render: () => <PerformanceSlide module="bwb" /> },
    { id: "advisor", label: BWB_LABEL.advisor, render: () => <AdvisorSlide module="bwb" /> },
    {
      id: "positions",
      label: BWB_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">{isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}</div>
      ),
    },
    { id: "history", label: BWB_LABEL.history, render: () => <HistoryTab /> },
    { id: "guide", label: BWB_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
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
      integrity={<IntegrityStrip data={data} updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity.measurementBreaks.length ?? 0) > 0 || (data?.integrity.schemaDrift.length ?? 0) > 0}
    />
  );
}
