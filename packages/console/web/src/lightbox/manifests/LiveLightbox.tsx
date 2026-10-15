import { LivePage, useLiveFlies } from "../../pages/Live/LivePage";
import { ManagePage } from "../../pages/Live/ManagePage";
import { MeicLivePage, useLiveMeic } from "../../pages/Live/MeicLivePage";
import { LoopPill } from "../../components/ScopeBar";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The Live page (2026-09-17): the flies live pilot's day, on the module frame since 2026-09-25.
 * One page, read-only, no order path. The header carries the arming strip -- armed for today or not, the
 * halt flag, the pilot's arm and symbol -- and the live loop's own pill, so the first glance says
 * whether anything can trade before the tiles say what it did. On the `meic` tab (2026-10-15) the
 * strip is MEIC's own arm record instead: each live loop is armed separately, so one badge for both
 * would say the wrong thing about one of them.
 *
 * Each live loop's tab carries its module's name (2026-10-15): flies' was `today` from when it was
 * the only live loop, beside `meic`. `/live/today` redirects to `flies` (navGroups' `legacy`).
 */
const slides: SlideDef[] = [
  { id: "flies", label: "flies", render: () => <LivePage /> },
  // MEIC's live loop: arming, halt, orphans, held submissions, open ICs and resolved history.
  { id: "meic", label: "meic", render: () => <MeicLivePage /> },
  // The daily position evaluation (packages/manage): every live trade against the ruleset.
  { id: "manage", label: "manage", render: () => <ManagePage /> },
];

function ArmBadge() {
  const { data } = useLiveFlies();
  if (!data) return null;
  const a = data.arm;
  const label = a.halted ? "HALTED" : a.armed ? "ARMED" : a.stale ? "stale arm" : "not armed";
  const cls = a.halted ? "pnl-neg" : a.armed ? "pnl-pos" : "muted";
  return (
    <span className={`chip ${cls}`} title={`live.enabled ${a.liveEnabled === null ? "unknown" : String(a.liveEnabled)}; arm record ${a.date ?? "none"}; halt flag ${a.halted ? "present" : "absent"}`}>
      {label} · {a.arm ?? "?"} · {a.symbol ?? "?"}
    </span>
  );
}

function MeicArmBadge() {
  const { data } = useLiveMeic();
  if (!data) return null;
  const a = data.arm;
  const label = a.halted ? "HALTED" : a.armed ? (a.pastDisarm ? "past disarm" : "ARMED") : a.stale ? "stale arm" : "not armed";
  const cls = a.halted ? "pnl-neg" : a.armed && !a.pastDisarm ? "pnl-pos" : "muted";
  return (
    <span
      className={`chip ${cls}`}
      title={`enable_live_trading ${a.liveEnabled === null ? "unknown" : String(a.liveEnabled)}; arm record ${a.date ?? "none"}; disarm ${a.disarmTime} ET; halt flag ${a.halted ? "present" : "absent"}`}
    >
      meic · {label} · {a.symbol ?? "?"}
    </span>
  );
}

function LivePill() {
  const { data } = useLiveFlies();
  const l = data?.loop;
  return <LoopPill state={l?.state} ageSeconds={l?.ageSeconds} detail={l?.lastIterationAt != null ? `last live iteration ${l.lastIterationAt}` : undefined} />;
}

export function LiveLightbox({ slide }: { slide: string }) {
  const meic = slide === "meic";
  return (
    <ModuleFrame
      module="live"
      slide={slide}
      slides={slides}
      session={null}
      badge={meic ? <MeicArmBadge /> : <ArmBadge />}
      loopPill={meic ? undefined : <LivePill />}
    />
  );
}
