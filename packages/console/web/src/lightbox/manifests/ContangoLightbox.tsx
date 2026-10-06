import { useContango, useContangoMetrics } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import {
  ContangoArms,
  ContangoCosts,
  ContangoDecisions,
  ContangoHelp,
  ContangoHistory,
  ContangoPerformance,
  ContangoPositions,
  ContangoRegime,
  ContangoToday,
} from "../../pages/Contango/ContangoPages";
import { ModuleFrame } from "../ModuleFrame";
import { CONTANGO_SLIDES, type ContangoSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const LABEL = Object.fromEntries(CONTANGO_SLIDES.map((s) => [s.id, s.label])) as Record<ContangoSlideId, string>;

/**
 * contango (the VIX/VIX3M switch held in shares), on the module frame. The ledger read
 * (`/api/contango`) feeds the session, regime, costs and tables; the module's own analytics
 * (`/api/contango/metrics`, `core.metrics.nav`) feed the arms and performance pages. The loop acts
 * once a session, inside its window, so an idle loop pill outside it is the design.
 */
export function ContangoLightbox({ slide }: { slide: string }) {
  const { data, isLoading } = useContango();
  const metrics = useContangoMetrics();
  const iteration = data?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: ContangoSlideId }> = [
    { id: "session", label: LABEL.session, render: () => <ContangoToday data={data} metrics={metrics.data} loading={isLoading} /> },
    { id: "regime", label: LABEL.regime, render: () => <ContangoRegime data={data} /> },
    { id: "decisions", label: LABEL.decisions, render: () => <ContangoDecisions /> },
    { id: "arms", label: LABEL.arms, render: () => <ContangoArms data={data} metrics={metrics.data} /> },
    { id: "performance", label: LABEL.performance, render: () => <ContangoPerformance data={data} metrics={metrics.data} /> },
    { id: "costs", label: LABEL.costs, render: () => <ContangoCosts data={data} /> },
    { id: "positions", label: LABEL.positions, render: () => <ContangoPositions data={data} /> },
    { id: "history", label: LABEL.history, render: () => <ContangoHistory data={data} /> },
    { id: "guide", label: LABEL.guide, render: () => <ContangoHelp data={data} /> },
  ];

  return (
    <ModuleFrame
      module="contango"
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
