import { useState } from "react";
import { useCalendars, useCalendarsPolicies } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard } from "../../components/DataTable";
import { LoopPill, TabStrip } from "../../components/ScopeBar";
import { IntegrityStrip } from "./IntegrityStrip";
import { BookComparison, EntryWindowCard, PlanCard, PositionsCard } from "./WeekCards";
import { PoliciesTab } from "./PoliciesTab";
import { WeeksTab } from "./WeeksTab";
import { HelpTab } from "./HelpTab";

type CalendarsTab = "week" | "policies" | "weeks" | "help";

const TABS = ["week", "policies", "weeks", "help"] as const;

/**
 * Weekly SPY double calendars.
 *
 * No mode toggle, and that is structural rather than a default: the module has no live loop and no
 * live store, so there is no second book a toggle could reach.
 *
 * The week tab leads with the ENTRY WINDOW rather than with positions or a net, which is the
 * opposite of every other module page here and is the right way round for this one. Entry is
 * unconditional by design, so a week holding nothing is never "no setup" — it is a refusal, and for
 * a strategy that gets one entry attempt per week a refusal is the most consequential thing the
 * module can report. The module's first scheduled Monday is the case in point: it took no position,
 * and the only honest headline for that day is the fifteen minutes of stale quotes that caused it.
 *
 * The measurement-integrity strip sits above all of it, ahead of any number, because the module's
 * seventh honesty rule is that its ranking never travels without the validation that says whether
 * to believe it — and a caveat met after the number has already been read arrived too late.
 */
export function CalendarsPage() {
  const [tab, setTab] = useState<CalendarsTab>("week");
  const { data, isLoading, isError, dataUpdatedAt } = useCalendars();
  // Fetched on every tab, not just the policy one: the validation verdict leads the integrity strip,
  // and a strip that only knows whether to trust the table once you have opened the table is useless.
  const { data: policies } = useCalendarsPolicies();

  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  // Open positions whose week is not the one on screen -- see the card below.
  const thisWeekIds = new Set((data?.currentWeek.positions ?? []).map((p) => p.positionId));
  const carriedOver = (data?.openPositions ?? []).filter((p) => !thisWeekIds.has(p.positionId));

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Calendars</h1>
        <PaperLiveBadge mode="paper" />
        <TabStrip tabs={TABS} value={tab} onChange={setTab} ariaLabel="calendars tabs" />
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={data?.today.lastIteration?.ageSeconds ?? null}
          detail={
            data?.today.lastIteration == null
              ? "no loop iterations recorded"
              : `${data.today.lastIteration.phase} · ${data.today.lastIteration.status}`
          }
        />
        {data?.session != null && <span className="muted">session {data.session}</span>}
      </div>

      {data !== undefined && !data.dbPresent ? (
        <div className="cards cards-wide">
          <Card title="Calendars" collapseKey="cal-absent">
            <p className="muted">
              This module has not run on this machine — there is no paper store at{" "}
              <span className="mono">~/.cherrypick/data/calendars/paper_trades.db</span> yet. Nothing is wrong;
              the page fills in after its first session.
            </p>
          </Card>
        </div>
      ) : (
        <>
          {tab === "week" && (
            <div className="cards cards-wide">
              <IntegrityStrip data={data} policies={policies} updatedAt={dataUpdatedAt} />
              <PlanCard data={data} updatedAt={dataUpdatedAt} />
              <EntryWindowCard data={data} updatedAt={dataUpdatedAt} />

              <PositionsCard
                title="structures this week"
                positions={data?.currentWeek.positions ?? []}
                emptyText="no structure was opened for this week"
                loading={isLoading}
                updatedAt={dataUpdatedAt}
              />

              {/* Only what "structures this week" does not already show.
                  The two cards ask different questions -- `week_of = this week` keeps a position
                  after it closes, `status != closed` keeps it after its week ends -- but for most
                  of a week those answers are the same rows, and two identical tables read as a bug
                  rather than as two views. The `path` book is why the second one has to exist at
                  all: it holds every leg to expiry and its back leg lands the FOLLOWING week, so it
                  routinely outlives the week that opened it. */}
              {carriedOver.length > 0 && (
                <PositionsCard
                  title="open trades carried from earlier weeks"
                  positions={carriedOver}
                  emptyText="nothing carried"
                  updatedAt={dataUpdatedAt}
                />
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
                    The collapsed narrative journal: a gate that blocks all morning is one row with a count,
                    not four hundred rows.
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
          )}

          {tab === "policies" && <PoliciesTab />}
          {tab === "weeks" && <WeeksTab data={data} />}
          {tab === "help" && <HelpTab data={data} />}
        </>
      )}
    </div>
  );
}
