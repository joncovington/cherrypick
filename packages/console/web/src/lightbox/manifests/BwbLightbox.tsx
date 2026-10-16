import { useBwb } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Bwb/IntegrityStrip";
import { BookComparison, FireCountsCard, OpenTradesCard } from "../../pages/Bwb/CurrentStateCards";
import { BwbSession } from "../../pages/Bwb/BwbSession";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HistoryTab } from "../../pages/Bwb/HistoryTab";
import { HelpTab } from "../../pages/Bwb/HelpTab";
import { BwbLiveGapSlide } from "../../pages/Bwb/LiveGapSlide";
import { BwbVolSlide } from "../../pages/Bwb/VolSlide";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { BWB_SLIDES, type BwbSlideId } from "../navGroups";
import type { SlideDef } from "../types";
import { ModuleSummary } from "../../components/summary/ModuleSummary";

const BWB_LABEL = Object.fromEntries(BWB_SLIDES.map((s) => [s.id, s.label])) as Record<BwbSlideId, string>;

/**
 * bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment), on the
 * module frame since 2026-09-25: a left rail of pages, and nothing on the surface opens an overlay.
 *
 * Paper by default, with the live pilot's book behind the page's paper/live toggle: every slide
 * that reads a ledger follows it. The advisor slide stays paper -- advised books exist only there.
 */
export function BwbLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  const { data, isLoading, dataUpdatedAt } = useBwb(mode);
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: BwbSlideId }> = [
    { id: "summary", label: BWB_LABEL.summary, render: () => <ModuleSummary module="bwb" mode={mode} /> },
    { id: "session", label: BWB_LABEL.session, render: () => <BwbSession data={data} loading={isLoading} mode={mode} /> },
    {
      id: "decisions",
      label: BWB_LABEL.decisions,
      render: () => (
        <div className="cards cards-wide">
          <DecisionsCard module="bwb" mode={mode} />
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
    { id: "vol", label: BWB_LABEL.vol, render: () => <BwbVolSlide /> },
    { id: "livegap", label: BWB_LABEL.livegap, render: () => <BwbLiveGapSlide /> },
    { id: "performance", label: BWB_LABEL.performance, render: () => <PerformanceSlide module="bwb" mode={mode} /> },
    { id: "advisor", label: BWB_LABEL.advisor, render: () => <AdvisorSlide module="bwb" /> },
    {
      id: "positions",
      label: BWB_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">{isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}</div>
      ),
    },
    { id: "history", label: BWB_LABEL.history, render: () => <HistoryTab mode={mode} /> },
    { id: "guide", label: BWB_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
      module="bwb"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      headerControls={<ModeToggle mode={mode} onChange={setMode} />}
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
