import { useCalendars, useCalendarsPolicies } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard } from "../../components/DataTable";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Calendars/IntegrityStrip";
import { BookComparison, EntryWindowCard, PlanCard, PositionsCard } from "../../pages/Calendars/WeekCards";
import { CalendarsSession } from "../../pages/Calendars/CalendarsSession";
import { PoliciesTab } from "../../pages/Calendars/PoliciesTab";
import { WeeksTab } from "../../pages/Calendars/WeeksTab";
import { HelpTab } from "../../pages/Calendars/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { CALENDARS_SLIDES, type CalendarsSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const CALENDARS_LABEL = Object.fromEntries(CALENDARS_SLIDES.map((s) => [s.id, s.label])) as Record<CalendarsSlideId, string>;

/**
 * Weekly SPY double calendars, on the module frame since 2026-09-25: a left rail of pages, and
 * nothing on the surface opens an overlay. No mode toggle -- structural: no live loop, no live store.
 */
export function CalendarsLightbox({ slide }: { slide: string }) {
  const { data, isLoading, isError, dataUpdatedAt } = useCalendars();
  const { data: policies } = useCalendarsPolicies();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";
  const thisWeekIds = new Set((data?.currentWeek.positions ?? []).map((p) => p.positionId));
  const carriedOver = (data?.openPositions ?? []).filter((p) => !thisWeekIds.has(p.positionId));
  // A store the module has never produced has nothing to say on any page; the session page says why.
  const absent = data !== undefined && !data.dbPresent;

  const slides: Array<SlideDef & { id: CalendarsSlideId }> = [
    { id: "session", label: CALENDARS_LABEL.session, render: () => <CalendarsSession data={data} loading={isLoading} /> },
    {
      id: "plan",
      label: CALENDARS_LABEL.plan,
      render: () => (
        <div className="cards cards-wide">
          <PlanCard data={data} updatedAt={dataUpdatedAt} />
          <EntryWindowCard data={data} updatedAt={dataUpdatedAt} />
        </div>
      ),
    },
    {
      id: "decisions",
      label: CALENDARS_LABEL.decisions,
      render: () => (
        <DataCard
          title="decisions today"
          headers={["arm", "reason", "occurrences", "last"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.today.decisions.length ?? 0}
          numFrom={2}
          empty="the journal recorded nothing on this session"
          updatedAt={dataUpdatedAt}
          footer={
            <p className="integrity-note">
              The collapsed narrative journal: a gate that blocks all morning is one row with a count, not four hundred rows.
            </p>
          }
        >
          {data?.today.decisions.map((d) => (
            <tr key={`${d.arm}-${d.reason}-${String(d.accepted)}`}>
              <td className="mono">{d.arm}</td>
              <td>
                <span className="mono">{d.reason}</span>
                {d.accepted && <span className="chip chip-ok integrity-chip">accepted</span>}
              </td>
              <td>{d.occurrences.toLocaleString()}</td>
              <td className="mono muted">{d.lastTs?.slice(11, 16) ?? "—"}</td>
            </tr>
          ))}
        </DataCard>
      ),
    },
    {
      id: "arms",
      label: CALENDARS_LABEL.arms,
      render: () => (
        <div className="cards cards-wide">
          <BookComparison data={data} updatedAt={dataUpdatedAt} />
        </div>
      ),
    },
    { id: "policies", label: CALENDARS_LABEL.policies, render: () => <PoliciesTab /> },
    { id: "performance", label: CALENDARS_LABEL.performance, render: () => <PerformanceSlide module="calendars" /> },
    { id: "advisor", label: CALENDARS_LABEL.advisor, render: () => <AdvisorSlide module="calendars" /> },
    {
      id: "positions",
      label: CALENDARS_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">
          <PositionsCard
            title="structures this week"
            positions={data?.currentWeek.positions ?? []}
            emptyText="no structure was opened for this week"
            loading={isLoading}
            updatedAt={dataUpdatedAt}
          />
          {carriedOver.length > 0 && (
            <PositionsCard title="open trades carried from earlier weeks" positions={carriedOver} emptyText="nothing carried" updatedAt={dataUpdatedAt} />
          )}
        </div>
      ),
    },
    { id: "history", label: CALENDARS_LABEL.history, render: () => <WeeksTab data={data} /> },
    { id: "guide", label: CALENDARS_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
      module="calendars"
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
      integrity={absent ? undefined : <IntegrityStrip data={data} policies={policies} updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity.measurementBreaks.length ?? 0) > 0 || (data?.integrity.schemaDrift.length ?? 0) > 0}
    />
  );
}
