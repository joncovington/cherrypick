import { ChangesSlide } from "../../components/changes/ChangesSlide";
import { ModuleArmPage } from "../../components/armpage/ModuleArmPage";
import { Navigate, useParams } from "react-router-dom";
import { ModeToggle } from "../../components/ModeToggle";
import { useMode } from "../../lib/useMode";
import { SharedCosts } from "../../components/costs/SharedCosts";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { useLadder, useLadderEquity, useLadderLive, useLadderLiveStatus, useLadderStatus, useLadderSummary } from "../../lib/api";
import { LadderEquityCard, equityOrder } from "../../pages/Ladder/LadderEquityCard";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { useEffect, useState } from "react";
import { LoopPill, ScopeSelect } from "../../components/ScopeBar";
import { LadderLiveHistory } from "../../pages/Ladder/LadderLiveHistory";
import { LadderLiveLots, LadderLiveToday, LadderNoLiveRead, LiveModeBadge } from "../../pages/Ladder/LadderLive";
import { LadderArms, LadderDecisions, LadderHelp, LadderHistory, LadderPositions, LadderToday } from "../../pages/Ladder/LadderPages";
import { ModuleFrame } from "../ModuleFrame";
import { LADDER_SLIDES, type LadderSlideId } from "../navGroups";
import type { SlideDef } from "../types";

const LABEL = Object.fromEntries(LADDER_SLIDES.map((s) => [s.id, s.label])) as Record<LadderSlideId, string>;

/** The paper loop ticks about once per minute inside its hours; a few minutes without one is idle. */
const LIVE_WITHIN_SECONDS = 300;

/**
 * ladder (the share dip ladder on leveraged index funds: a paper book, and a live view that only reads), on the module frame. The ledger read
 * (`/api/ladder`) feeds the lots and the loop's last tick; the module's own CLI answers (`status`,
 * `summary`) feed the next buy levels and the per-arm figures. The loop gates its own hours, so an idle
 * loop pill overnight is the design.
 *
 * Paper by default, with the live path behind the page's paper/live selector (`?mode=live`), as on the
 * other modules that have one. Live swaps the today, open lots and history tabs for the live loop's own
 * cards and ledger, and the tabs with no live read for one line; the selector changes what is shown
 * and nothing else. The old `live` tab is today in live mode.
 */
export function LadderLightbox({ slide }: { slide: string }) {
  if (slide === "live") return <Navigate to="/ladder/summary?mode=live" replace />;
  return <LadderFrame slide={slide} />;
}

/** The tabs that have a live read (help is the same page in either mode); every other tab says it has none. */
const LIVE_TABS = new Set<string>(["summary", "positions", "history", "guide"]);

/** Under Live, a tab with no live read is one line, never the paper book's figures under a live label. */
export function liveModeSlides<T extends SlideDef>(slides: T[]): T[] {
  return slides.map((s) => (LIVE_TABS.has(s.id) ? s : { ...s, render: () => <LadderNoLiveRead /> }));
}

function LadderFrame({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  // `/<module>/arm/<name>`: the arm's own page.
  const armPage = useParams()["name"] ?? null;
  const { data, isLoading } = useLadder();
  const status = useLadderStatus();
  const summary = useLadderSummary();
  const equity = useLadderEquity();
  // The performance tab's arm picker: null (the default) is every arm. It narrows the realized-and-marked
  // card and the shared slide beneath it; no other ladder tab takes an arm, so it shows on this tab only.
  const [arm, setArm] = useState<string | null>(null);
  const armOptions = equityOrder(data?.arms, equity.data);
  useEffect(() => {
    if (arm !== null && data !== undefined && equity.data !== undefined && !armOptions.includes(arm)) setArm(null);
  }, [arm, armOptions, data, equity.data]);
  const live = useLadderLive();
  const liveStatus = useLadderLiveStatus();
  // The pill follows the selector: live shows the LIVE loop's last tick (its own ledger), never the paper loop's.
  const onLive = mode === "live";
  const pillSource = onLive ? live.data : data;
  const iteration = pillSource?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < LIVE_WITHIN_SECONDS ? "live" : "idle";

  const slides: Array<SlideDef & { id: LadderSlideId }> = [
    {
      id: "summary",
      label: LABEL.summary,
      render: () => (onLive ? <LadderLiveToday live={live.data} status={liveStatus.data} /> : <LadderToday data={data} status={status.data} loading={isLoading} />),
    },
    {
      id: "positions",
      label: LABEL.positions,
      render: () => (onLive ? <LadderLiveLots live={live.data} status={liveStatus.data} /> : <LadderPositions data={data} />),
    },
    {
      id: "history",
      label: LABEL.history,
      render: () => (onLive ? <LadderLiveHistory live={live.data} status={liveStatus.data} /> : <LadderHistory data={data} />),
    },
    {
      id: "arms",
      label: LABEL.arms,
      render: () =>
        armPage !== null ? (
          <ModuleArmPage module="ladder" name={armPage} />
        ) : (
          <LadderArms summary={summary.data} status={status.data} decl={data?.arms} />
        ),
    },
    { id: "decisions", label: LABEL.decisions, render: () => <LadderDecisions /> },
    {
      id: "performance",
      label: LABEL.performance,
      render: () => (
        <>
          <div className="pf-layout">
            <LadderEquityCard equity={equity.data} order={armOptions} arm={arm} />
          </div>
          <PerformanceSlide module="ladder" pairs={false} armCards={false} arm={arm} />
        </>
      ),
    },
    { id: "costs", label: LABEL.costs, render: () => <SharedCosts module="ladder" /> },
    { id: "changes", label: LABEL.changes, render: () => <ChangesSlide module="ladder" /> },
    { id: "guide", label: LABEL.guide, render: () => <LadderHelp status={status.data} /> },
  ];

  const shown = onLive ? liveModeSlides(slides) : slides;

  return (
    <ModuleFrame
      module="ladder"
      slide={slide}
      slides={shown}
      headerControls={
        <>
          {!onLive && slide === "performance" && armPage === null && (
            <ScopeSelect label="arm" value={arm} options={armOptions} onChange={setArm} allLabel="all arms" />
          )}
          <ModeToggle mode={mode} onChange={setMode} />
        </>
      }
      badge={onLive ? <LiveModeBadge status={liveStatus.data} /> : <PaperLiveBadge mode="paper" />}
      loopPill={
        <LoopPill
          state={pillSource === undefined ? undefined : loopState}
          ageSeconds={iteration?.ageSeconds ?? null}
          detail={
            iteration === null
              ? onLive
                ? "no live loop iterations recorded"
                : "no loop iterations recorded"
              : `${onLive ? "live loop · " : ""}${iteration.phase} · ${iteration.status}`
          }
        />
      }
      session={onLive ? null : (data?.session ?? null)}
      integrityAttention={(data?.measurementBreaks.length ?? 0) > 0}
    />
  );
}
