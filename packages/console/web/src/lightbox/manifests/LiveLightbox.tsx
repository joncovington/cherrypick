import { LivePage, useLiveFlies } from "../../pages/Live/LivePage";
import { PortfolioPage, PositionsPage, WheelPage } from "../../pages/Live/ManagePage";
import { MeicLivePage, useLiveMeic } from "../../pages/Live/MeicLivePage";
import { LiveTodayPage, suiteBadgeText, useLiveToday } from "../../pages/Live/LiveTodayPage";
import { LoopPill } from "../../components/ScopeBar";
import { AlertDaemonChip } from "../../components/AlertDaemonChip";
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
 * the only live loop, beside `meic`. The same day `today` became the all-loops summary, first: one row
 * per live loop (flies, meic, bwb), the attention list and the suite totals, with a suite badge in
 * the header ("2 armed · HALTED") rather than one loop's.
 *
 * The flies order-alert daemon's chip sits in the header on every tab (2026-10-16), moved here
 * when the Overview page that carried it was removed and `/` began redirecting to this page.
 *
 * 2026-10-18: the `manage` tab split in two, after `today`: `positions` (each trade's action, actions
 * first) and `portfolio` (the account as one book). Both are account-wide, so they carry the suite
 * badge, as `today` does; `/live/manage` lands on `positions`. 2026-10-20: `wheel`, the third view of
 * the same evaluation (each wheel cycle on the account), beside them and account-wide too.
 */
const slides: SlideDef[] = [
  // Every live loop on one page: what is armed, open and settled, and what needs a person.
  { id: "today", label: "today", render: () => <LiveTodayPage /> },
  // The daily position evaluation (packages/manage): every live trade against the ruleset.
  { id: "positions", label: "positions", render: () => <PositionsPage /> },
  // The same evaluation read as one book: greeks against their bands, scenarios, guidance, sizing.
  { id: "portfolio", label: "portfolio", render: () => <PortfolioPage /> },
  // 2026-10-20: each wheel cycle on the account (packages/manage wheel.py) and its wheel rules.
  { id: "wheel", label: "wheel", render: () => <WheelPage /> },
  { id: "flies", label: "flies", render: () => <LivePage /> },
  // MEIC's live loop: arming, halt, orphans, held submissions, open ICs and resolved history.
  { id: "meic", label: "meic", render: () => <MeicLivePage /> },
];

/** The tabs that read the whole account, not one loop: they wear the suite's badge. */
const ACCOUNT_WIDE = new Set(["today", "positions", "portfolio", "wheel"]);

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

function SuiteBadge() {
  const { data } = useLiveToday();
  if (!data) return null;
  const n = data.suite.armed.length;
  const cls = data.halted ? "pnl-neg" : n > 0 ? "pnl-pos" : "muted";
  const attention = data.attention.length;
  return (
    <span
      className={`chip ${cls}`}
      title={
        `armed now: ${n > 0 ? data.suite.armed.join(", ") : "none"} of ${data.suite.loops} live loops; ` +
        `halt flag ${data.halted ? "present" : "absent"}; ${attention} item${attention === 1 ? "" : "s"} needing a person`
      }
    >
      {suiteBadgeText(data)}
      {attention > 0 ? ` · ${attention} need${attention === 1 ? "s" : ""} a person` : ""}
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
  const today = ACCOUNT_WIDE.has(slide);
  return (
    <ModuleFrame
      module="live"
      slide={slide}
      slides={slides}
      session={null}
      badge={today ? <SuiteBadge /> : meic ? <MeicArmBadge /> : <ArmBadge />}
      loopPill={meic || today ? undefined : <LivePill />}
      headerControls={<AlertDaemonChip />}
    />
  );
}
