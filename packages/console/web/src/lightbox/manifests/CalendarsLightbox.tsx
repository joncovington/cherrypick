import { useCalendars, useCalendarsPolicies } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard } from "../../components/DataTable";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Calendars/IntegrityStrip";
import { BookComparison, EntryWindowCard, PlanCard, PositionsCard } from "../../pages/Calendars/WeekCards";
import { PoliciesTab } from "../../pages/Calendars/PoliciesTab";
import { WeeksTab } from "../../pages/Calendars/WeeksTab";
import { HelpTab } from "../../pages/Calendars/HelpTab";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/** Weekly SPY double calendars. No mode toggle -- structural: no live loop, no live store. */
export function CalendarsLightbox({ slide }: { slide: string }) {
  const { data, isLoading, isError, dataUpdatedAt } = useCalendars();
  const { data: policies } = useCalendarsPolicies();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";
  const thisWeekIds = new Set((data?.currentWeek.positions ?? []).map((p) => p.positionId));
  const carriedOver = (data?.openPositions ?? []).filter((p) => !thisWeekIds.has(p.positionId));

  // The dbPresent gate wraps EVERY slide here, not just "now" -- the module comment's own reason:
  // there is nothing to say about policies or weeks a store has never produced.
  const absent = data !== undefined && !data.dbPresent;

  const slides: SlideDef[] = absent
    ? [
        {
          id: "now",
          label: "now",
          render: () => (
            <div className="cards cards-wide">
              <Card title="Calendars" collapseKey="cal-absent">
                <p className="muted">
                  This module has not run on this machine — there is no paper store at{" "}
                  <span className="mono">~/.cherrypick/data/calendars/paper_trades.db</span> yet.
                  Nothing is wrong; the page fills in after its first session.
                </p>
              </Card>
            </div>
          ),
        },
      ]
    : [
        {
          id: "now",
          label: "now",
          render: () => (
            <div className="cards cards-wide">
              <PlanCard data={data} updatedAt={dataUpdatedAt} />
              <EntryWindowCard data={data} updatedAt={dataUpdatedAt} />
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
              <DataCard
                title="decisions today"
                headers={["book", "reason", "occurrences", "last"]}
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
                  <tr key={`${d.book}-${d.reason}-${String(d.accepted)}`}>
                    <td className="mono">{d.book}</td>
                    <td>
                      <span className="mono">{d.reason}</span>
                      {d.accepted && <span className="chip chip-ok integrity-chip">accepted</span>}
                    </td>
                    <td>{d.occurrences.toLocaleString()}</td>
                    <td className="mono muted">{d.lastTs?.slice(11, 16) ?? "—"}</td>
                  </tr>
                ))}
              </DataCard>
              <BookComparison data={data} updatedAt={dataUpdatedAt} />
            </div>
          ),
        },
        { id: "policies", label: "policies", render: () => <PoliciesTab /> },
        { id: "weeks", label: "weeks", render: () => <WeeksTab data={data} /> },
        { id: "guide", label: "help", render: () => <HelpTab data={data} /> },
      ];

  return (
    <LightboxFrame
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
