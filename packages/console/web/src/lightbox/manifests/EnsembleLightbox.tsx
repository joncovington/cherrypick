import { useEnsemble, useEnsemblePerformance } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import {
  EnsembleCosts,
  EnsembleDecisions,
  EnsembleHelp,
  EnsembleHistory,
  EnsemblePerformancePage,
  EnsemblePositions,
  EnsembleToday,
} from "../../pages/Ensemble/EnsemblePages";
import { ModuleFrame } from "../ModuleFrame";
import { ENSEMBLE_SLIDES, type EnsembleSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const LABEL = Object.fromEntries(ENSEMBLE_SLIDES.map((s) => [s.id, s.label])) as Record<EnsembleSlideId, string>;

/**
 * ensemble (item 75's arms, paper), on the module frame. One ledger read (`/api/ensemble`) feeds the
 * session, the register and the open structures; the closed trades and each arm's net and drawdown have
 * their own reads. The loop acts in each arm's entry window and marks open structures through the day,
 * so an idle loop pill outside a session is the design.
 */
export function EnsembleLightbox({ slide }: { slide: string }) {
  const { data, isLoading } = useEnsemble();
  const perf = useEnsemblePerformance();
  const iteration = data?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: EnsembleSlideId }> = [
    { id: "summary", label: LABEL.summary, render: () => <EnsembleToday data={data} loading={isLoading} /> },
    { id: "positions", label: LABEL.positions, render: () => <EnsemblePositions data={data} /> },
    { id: "history", label: LABEL.history, render: () => <EnsembleHistory data={data} /> },
    { id: "decisions", label: LABEL.decisions, render: () => <EnsembleDecisions /> },
    { id: "performance", label: LABEL.performance, render: () => <EnsemblePerformancePage perf={perf.data} /> },
    { id: "costs", label: LABEL.costs, render: () => <EnsembleCosts perf={perf.data} /> },
    { id: "guide", label: LABEL.guide, render: () => <EnsembleHelp /> },
  ];

  return (
    <ModuleFrame
      module="ensemble"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode="paper" />}
      loopPill={
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={iteration?.ageSeconds ?? null}
          detail={iteration === null ? "no loop iterations recorded" : `${iteration.phase} · ${iteration.status}`}
        />
      }
      session={data?.session ?? null}
      integrityAttention={(data?.measurementBreaks.length ?? 0) > 0}
    />
  );
}
