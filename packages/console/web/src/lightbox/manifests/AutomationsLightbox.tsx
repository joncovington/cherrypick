import { useAutomations, useAutomationsPerformance } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import {
  AutomationsCosts,
  AutomationsDecisions,
  AutomationsHelp,
  AutomationsHistory,
  AutomationsPerformancePage,
  AutomationsPositions,
  AutomationsToday,
} from "../../pages/Automations/AutomationsPages";
import { AutomationsGroupsPage } from "../../pages/Automations/AutomationsGroups";
import { ModuleFrame } from "../ModuleFrame";
import { AUTOMATIONS_SLIDES, type AutomationsSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const LABEL = Object.fromEntries(AUTOMATIONS_SLIDES.map((s) => [s.id, s.label])) as Record<AutomationsSlideId, string>;

/**
 * automations (item 75's arms, paper), on the module frame. One ledger read (`/api/automations`) feeds the
 * session, the register and the open structures; the closed trades and each arm's net and drawdown have
 * their own reads. The loop acts in each arm's entry window and marks open structures through the day,
 * so an idle loop pill outside a session is the design.
 */
export function AutomationsLightbox({ slide }: { slide: string }) {
  const { data, isLoading } = useAutomations();
  const perf = useAutomationsPerformance();
  const iteration = data?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: AutomationsSlideId }> = [
    { id: "summary", label: LABEL.summary, render: () => <AutomationsToday data={data} loading={isLoading} /> },
    { id: "groups", label: LABEL.groups, render: () => <AutomationsGroupsPage /> },
    { id: "positions", label: LABEL.positions, render: () => <AutomationsPositions data={data} /> },
    { id: "history", label: LABEL.history, render: () => <AutomationsHistory data={data} /> },
    { id: "decisions", label: LABEL.decisions, render: () => <AutomationsDecisions /> },
    { id: "performance", label: LABEL.performance, render: () => <AutomationsPerformancePage perf={perf.data} /> },
    { id: "costs", label: LABEL.costs, render: () => <AutomationsCosts perf={perf.data} /> },
    { id: "guide", label: LABEL.guide, render: () => <AutomationsHelp /> },
  ];

  return (
    <ModuleFrame
      module="automations"
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
