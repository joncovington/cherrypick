import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { OptionsFlowDay } from "@console/shared";
import { useOptionsFlow } from "../../lib/api";
import { FlowBirdseye, FlowDerived, FlowSpreads, FlowToday, FlowTrades, FlowVolOi } from "../../pages/Flow/FlowPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Options flow (2026-10-03): QuikOptions' Hot Options Report, one capture a session. The session is
 * the URL's `?session=` so every card link and a shared address open the same day; without one the
 * page shows the latest capture. Shown only when `quikoptions.enabled` is on (`visibility.ts`).
 */
export function FlowLightbox({ slide }: { slide: string }) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("session") ?? undefined;
  const { data, isLoading, isError } = useOptionsFlow(requested);
  const day = data?.current ?? null;

  const body = (render: (d: OptionsFlowDay) => ReactNode) => (): ReactNode => {
    if (day !== null) return render(day);
    if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
    return (
      <section className="card">
        <div className="card-head">
          <h2>No capture to show</h2>
        </div>
        <p className="muted">
          {isError
            ? "The console could not read the Options flow store."
            : (data?.degraded?.reason ?? "Nothing has been captured for this session.")}
        </p>
      </section>
    );
  };

  const slides: SlideDef[] = [
    { id: "today", label: "today", render: body((d) => <FlowToday day={d} />) },
    { id: "derived", label: "derived flow", render: body((d) => <FlowDerived day={d} />) },
    { id: "birdseye", label: "birdseye", render: body((d) => <FlowBirdseye day={d} />) },
    { id: "trades", label: "trades", render: body((d) => <FlowTrades day={d} />) },
    { id: "spreads", label: "spreads", render: body((d) => <FlowSpreads day={d} />) },
    { id: "voloi", label: "vol / OI", render: body((d) => <FlowVolOi day={d} />) },
  ];

  const sessions = data?.sessions ?? [];
  const headerControls =
    sessions.length > 0 ? (
      <select
        className="chip review-session-select"
        value={day?.session ?? ""}
        onChange={(e) => {
          const next = new URLSearchParams(params);
          next.set("session", e.target.value);
          setParams(next);
        }}
        aria-label="Session"
      >
        {sessions
          .slice()
          .reverse()
          .map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
      </select>
    ) : null;

  return (
    <ModuleFrame
      module="flow"
      slide={slide}
      slides={slides}
      session={day?.session ?? null}
      headerControls={
        <>
          {headerControls}
          <span className="chip" title="QuikOptions' Hot Options Report, captured once after the close">
            source: QuikOptions
          </span>
        </>
      }
    />
  );
}
