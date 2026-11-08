import { useWeeklyput, useWeeklyputCycles, useWeeklyputHistory, useWeeklyputStatus, useWeeklyputSummary } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import {
  WeeklyputCosts,
  WeeklyputCycles,
  WeeklyputDecisions,
  WeeklyputHelp,
  WeeklyputHistory,
  WeeklyputPerformance,
  WeeklyputPositions,
  WeeklyputToday,
} from "../../pages/Weeklyput/WeeklyputPages";
import { ModuleFrame } from "../ModuleFrame";
import { WEEKLYPUT_SLIDES, type WeeklyputSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const LABEL = Object.fromEntries(WEEKLYPUT_SLIDES.map((s) => [s.id, s.label])) as Record<WeeklyputSlideId, string>;

/**
 * weeklyput (item 57's unhedged weekly index put, paper), on the module frame. The ledger read
 * (`/api/weeklyput`) feeds the session and the two put tables; the module's own CLI answers
 * (`status`, `cycles`, `summary`) feed the calendar, the judged per-cycle mean and the forward
 * read. The loop acts in a ten-minute window of an entry session and marks open puts through the
 * week, so an idle loop pill outside a session is the design.
 */
export function WeeklyputLightbox({ slide }: { slide: string }) {
  const { data, isLoading } = useWeeklyput();
  const status = useWeeklyputStatus();
  const cycles = useWeeklyputCycles();
  const summary = useWeeklyputSummary();
  const costs = useWeeklyputHistory({ arm: null, from: null, to: null });
  const iteration = data?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: WeeklyputSlideId }> = [
    { id: "summary", label: LABEL.summary, render: () => <WeeklyputToday data={data} status={status.data} loading={isLoading} /> },
    { id: "cycles", label: LABEL.cycles, render: () => <WeeklyputCycles cycles={cycles.data} status={status.data} /> },
    { id: "positions", label: LABEL.positions, render: () => <WeeklyputPositions data={data} /> },
    { id: "history", label: LABEL.history, render: () => <WeeklyputHistory data={data} /> },
    { id: "decisions", label: LABEL.decisions, render: () => <WeeklyputDecisions /> },
    { id: "performance", label: LABEL.performance, render: () => <WeeklyputPerformance summary={summary.data} cycles={cycles.data} /> },
    { id: "costs", label: LABEL.costs, render: () => <WeeklyputCosts summary={summary.data} history={costs.data?.rows} /> },
    { id: "guide", label: LABEL.guide, render: () => <WeeklyputHelp status={status.data} /> },
  ];

  return (
    <ModuleFrame
      module="weeklyput"
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
