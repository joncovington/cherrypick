import { useSearchParams } from "react-router-dom";
import type { DerivedFlowRow, FlowEvent } from "@console/shared";
import { GridCard } from "../../components/grid/GridCard";
import { useOptionsFlow } from "../../lib/api";
import { fmtCount, fmtDollarsShort, fmtNum } from "../../lib/format";
import { DerivedTable, FlagCell, FlagKey, NetByName } from "./FlowPage";

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
              <td title="high impact">
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

/** The day's spreads by size (the order they are listed in, largest first), in the derived flow's
 *  words: date, strike, kind; read; Δ$ and score; the flags as symbols, keyed below. */
export function SpreadsPostTable({ rows }: { rows: DerivedFlowRow[] }) {
  const max = rows.reduce((m, r) => Math.max(m, Math.abs(r.score ?? 0)), 0) || 1;
  return (
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th className="num">#</th>
          <th>Spread</th>
          <th className="num">Size</th>
          <th>Read</th>
          <th className="num">Premium</th>
          <th className="num">Δ$</th>
          <th className="num">Score</th>
          <th />
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const s = r.confirmedScore ?? r.score;
          const tone = r.view === "bullish" ? "pnl-pos" : r.view === "bearish" ? "pnl-neg" : "muted";
          const w = s === null ? 0 : (Math.abs(s) / max) * 28;
          return (
            <tr key={`${r.symbol}-${String(i)}`}>
              <td className="num muted">{i + 1}</td>
              <td>
                {r.symbol} {r.what ?? ""} <span className="muted">{r.kindLabel}</span>
              </td>
              <td className="num">{fmtCount(r.size)}</td>
              <td className={tone}>
                {r.direction ?? "unread"}
                {r.view !== null ? `, ${r.view}` : ""}
              </td>
              <td className="num">{fmtDollarsShort(r.premium)}</td>
              <td className="num">{fmtDollarsShort(r.deltaDollars)}</td>
              <td className={`num ${tone}`}>{s === null ? "—" : `${s > 0 ? "+" : ""}${fmtNum(s, 0)}`}</td>
              <td>
                <svg width={56} height={8} aria-hidden="true">
                  <rect width={56} height={8} fill="var(--row-line)" />
                  <rect x={s !== null && s < 0 ? 28 - w : 28} width={w} height={8} fill={s === null ? "none" : s >= 0 ? "var(--ok)" : "var(--err)"} />
                </svg>
              </td>
              <td>
                <FlagCell r={r} />
              </td>
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
          <FlagKey rows={d.flows.slice(0, 10)} />
        </GridCard>
        <GridCard label={`Net by name — ${day.session}`} span={12} h={304} className="post-card" foot="read flows' Δ$, weighted by purity">
          <NetByName names={d.names} each={4} />
        </GridCard>
        {(() => {
          const spreads = [...d.flows, ...d.unread]
            .filter((r) => r.kind === "spread")
            .sort((a, b) => (b.size ?? 0) - (a.size ?? 0))
            .slice(0, 10);
          return spreads.length === 0 ? null : (
            <GridCard
              label={`Top spreads — ${day.session}`}
              span={12}
              h={304}
              className="post-card"
              foot="the day's largest spreads by contracts; read from the signs of price and delta"
            >
              <SpreadsPostTable rows={spreads} />
              <FlagKey rows={spreads} />
            </GridCard>
          );
        })()}
        {day.events !== null && day.events.length > 0 && (
          <GridCard
            label={`Events — ${day.session}`}
            span={12}
            h={304}
            className="post-card"
            foot="high-impact releases: the day's with actual against estimate, then the week ahead"
          >
            <EventsTable events={day.events} session={day.session} />
          </GridCard>
        )}
      </div>
    </div>
  );
}
