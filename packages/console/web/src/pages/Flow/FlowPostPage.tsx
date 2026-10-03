import { useSearchParams } from "react-router-dom";
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
      </div>
    </div>
  );
}
