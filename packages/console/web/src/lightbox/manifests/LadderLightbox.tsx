import { ChangesSlide } from "../../components/changes/ChangesSlide";
import { useLadder, useLadderLive, useLadderLiveStatus, useLadderStatus, useLadderSummary } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import { LadderLive, LiveModeBadge } from "../../pages/Ladder/LadderLive";
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
 */
export function LadderLightbox({ slide }: { slide: string }) {
  const { data, isLoading } = useLadder();
  const status = useLadderStatus();
  const summary = useLadderSummary();
  const live = useLadderLive();
  const liveStatus = useLadderLiveStatus();
  // The pill follows the tab: the live tab shows the LIVE loop's last tick (its own ledger), never the paper loop's.
  const onLive = slide === "live";
  const pillSource = onLive ? live.data : data;
  const iteration = pillSource?.lastIteration ?? null;
  const loopState = iteration === null ? "no-data" : iteration.ageSeconds < LIVE_WITHIN_SECONDS ? "live" : "idle";

  const slides: Array<SlideDef & { id: LadderSlideId }> = [
    { id: "summary", label: LABEL.summary, render: () => <LadderToday data={data} status={status.data} loading={isLoading} /> },
    { id: "live", label: LABEL.live, render: () => <LadderLive live={live.data} status={liveStatus.data} /> },
    { id: "positions", label: LABEL.positions, render: () => <LadderPositions data={data} /> },
    { id: "history", label: LABEL.history, render: () => <LadderHistory data={data} /> },
    { id: "arms", label: LABEL.arms, render: () => <LadderArms summary={summary.data} status={status.data} decl={data?.arms} /> },
    { id: "decisions", label: LABEL.decisions, render: () => <LadderDecisions /> },
    { id: "changes", label: LABEL.changes, render: () => <ChangesSlide module="ladder" /> },
    { id: "guide", label: LABEL.guide, render: () => <LadderHelp status={status.data} /> },
  ];

  return (
    <ModuleFrame
      module="ladder"
      slide={slide}
      slides={slides}      badge={slide === "live" ? <LiveModeBadge status={liveStatus.data} /> : <PaperLiveBadge mode="paper" />}
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
