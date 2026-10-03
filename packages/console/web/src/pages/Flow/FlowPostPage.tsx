import { useSearchParams } from "react-router-dom";
import type { FlowEvent } from "@console/shared";
import { GridCard } from "../../components/grid/GridCard";
import { useOptionsFlow } from "../../lib/api";
import { DerivedTable, NetByName } from "./FlowPage";

/**
 * The post page (`/post/flow?session=…`, 2026-10-03): what the Discord series captures, and nothing
 * else. Linked from no nav and outside the shell — no header, no rail — at a fixed width that reads
 * on a phone once Discord scales it. The derived flow without the columns a post does not need (the
 * four factors, the open-interest verdict); the same capture's numbers as the derived flow tab.
 *
 * Card titles are `<name> — <session>` and each card draws an SVG, which `ui-check --card` needs;
 * `scripts/quikoptions_post.py` captures the Derived flow and Net by name cards from here, so a
 * renamed title breaks that post.
 */
function dayLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

/** The session's releases (actual against estimate) then the next week's, in the tables' style. */
export function EventsTable({ events, session }: { events: FlowEvent[]; session: string }) {
  return (
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Day</th>
          <th>Time ET</th>
          <th />
          <th>Event</th>
          <th className="num">Actual</th>
          <th className="num">Estimate</th>
          <th className="num">Previous</th>
        </tr>
      </thead>
      <tbody>
        {events.map((e, i) => {
          const today = e.date === session;
          return (
            <tr key={`${e.date}-${e.event}-${String(i)}`}>
              <td className={today ? undefined : "muted"}>{today ? "today" : dayLabel(e.date)}</td>
              <td className="muted">{e.timeEt ?? "—"}</td>
              <td title="high impact (the site's rating)">
                <svg width={8} height={8} aria-hidden="true">
                  <rect width={8} height={8} fill="var(--warn)" />
                </svg>
              </td>
              <td>{e.event}</td>
              <td className="num">{e.actual ?? (today ? "pending" : "—")}</td>
              <td className="num muted">{e.estimate ?? "—"}</td>
              <td className="num muted">{e.previous ?? "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function FlowPostPage() {
  const [params] = useSearchParams();
  const { data } = useOptionsFlow(params.get("session") ?? undefined);
  const day = data?.current ?? null;
  const d = day?.derived ?? null;
  if (day === null || d === null) {
    return (
      <div className="post-page">
        <p className="muted">{day === null ? "No capture for this session." : "Not scored yet for this session."}</p>
      </div>
    );
  }
  return (
    <div className="post-page">
      <div className="grid-12">
        <GridCard
          label={`Derived flow — ${day.session}`}
          span={12}
          h={304}
          className="post-card"
          foot="score: size × conviction × purity × opening, signed by the view · Δ$ = stock-equivalent exposure"
        >
          <DerivedTable rows={d.flows} limit={10} full={false} post />
        </GridCard>
        <GridCard label={`Net by name — ${day.session}`} span={12} h={304} className="post-card" foot="read flows' Δ$, weighted by purity">
          <NetByName names={d.names} each={4} />
        </GridCard>
        {day.events !== null && day.events.length > 0 && (
          <GridCard
            label={`Events — ${day.session}`}
            span={12}
            h={304}
            className="post-card"
            foot="high-impact releases, the site's rating: the day's with actual against estimate, then the week ahead"
          >
            <EventsTable events={day.events} session={day.session} />
          </GridCard>
        )}
      </div>
    </div>
  );
}
