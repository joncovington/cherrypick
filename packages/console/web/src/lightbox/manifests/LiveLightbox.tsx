import { LivePage, useLiveFlies } from "../../pages/Live/LivePage";
import { LoopPill } from "../../components/ScopeBar";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/**
 * The Live page (2026-09-17): the flies live pilot's day as a suite-level lightbox. One slide,
 * read-only, no order path. The header carries the arming strip -- armed for today or not, the
 * halt flag, the pilot's arm and symbol -- and the live loop's own pill, so the first glance says
 * whether anything can trade before the tiles say what it did.
 */
const slides: SlideDef[] = [{ id: "today", label: "today", render: () => <LivePage /> }];

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

function LivePill() {
  const { data } = useLiveFlies();
  const l = data?.loop;
  return <LoopPill state={l?.state} ageSeconds={l?.ageSeconds} detail={l?.lastIterationAt != null ? `last live iteration ${l.lastIterationAt}` : undefined} />;
}

export function LiveLightbox({ slide }: { slide: string }) {
  return <LightboxFrame module="live" slide={slide} slides={slides} session={null} badge={<ArmBadge />} loopPill={<LivePill />} />;
}
