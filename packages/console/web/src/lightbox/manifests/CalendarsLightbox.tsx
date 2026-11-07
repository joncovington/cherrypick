import { AnalysisSlide } from "../../components/analysis/AnalysisSlide";
import { useCalendars, useCalendarsPolicies } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
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
import { ModuleSummary } from "../../components/summary/ModuleSummary";
import { BookArmGuide } from "../../components/ExperimentGuide";

const DECISIONS_HELP: CardHelpContent = {
  title: "Decisions today",
  body: (
    <HelpSection title="The collapsed journal">
      <p>The collapsed narrative journal: a gate that blocks all morning is one row with a count, not four hundred rows.</p>
    </HelpSection>
  ),
};

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
    { id: "summary", label: CALENDARS_LABEL.summary, render: () => <ModuleSummary module="calendars" mode={"paper"} today={<CalendarsSession data={data} loading={isLoading} />} /> },
    {
      id: "plan",
      label: CALENDARS_LABEL.plan,
      render: () => (
        <div className="pf-layout">
          <Section concept="stand" tag="the plan" question="What is this week's plan?">
            <PlanCard data={data} updatedAt={dataUpdatedAt} />
          </Section>
          <Section concept="live" tag="entry day" question="What did the entry window do?">
            <EntryWindowCard data={data} updatedAt={dataUpdatedAt} />
          </Section>
        </div>
      ),
    },
    {
      id: "decisions",
      label: CALENDARS_LABEL.decisions,
      render: () => (
        <div className="pf-layout">
        <Section concept="live" tag="today" question="Why did today go the way it did?">
        <DataCard
          title="decisions today"
          help={DECISIONS_HELP}
          headers={["arm", "reason", "occurrences", "last"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.today.decisions.length ?? 0}
          numFrom={2}
          empty="the journal recorded nothing on this session"
          updatedAt={dataUpdatedAt}
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
        </Section>
        </div>
      ),
    },
    {
      id: "arms",
      label: CALENDARS_LABEL.arms,
      render: () => (
        <>
          <div className="pf-layout">
            <Section concept="edge" tag="the arms" question="How did each arm do?">
              <BookComparison data={data} updatedAt={dataUpdatedAt} />
            </Section>
          </div>
          <BookArmGuide module="calendars" />
        </>
      ),
    },
    { id: "policies", label: CALENDARS_LABEL.policies, render: () => <PoliciesTab /> },
    { id: "performance", label: CALENDARS_LABEL.performance, render: () => <PerformanceSlide module="calendars" /> },
    { id: "analysis", label: CALENDARS_LABEL.analysis, render: () => <AnalysisSlide module="calendars" /> },
    { id: "advisor", label: CALENDARS_LABEL.advisor, render: () => <AdvisorSlide module="calendars" /> },
    {
      id: "positions",
      label: CALENDARS_LABEL.positions,
      render: () => (
        <div className="pf-layout">
          <Section concept="stand" tag="this week" question="What was opened this week?">
            <PositionsCard
              title="structures this week"
              positions={data?.currentWeek.positions ?? []}
              emptyText="no structure was opened for this week"
              loading={isLoading}
              updatedAt={dataUpdatedAt}
            />
          </Section>
          {carriedOver.length > 0 && (
            <Section concept="room" tag="carried" question="What is still open from earlier weeks?">
              <PositionsCard title="open trades carried from earlier weeks" positions={carriedOver} emptyText="nothing carried" updatedAt={dataUpdatedAt} />
            </Section>
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
