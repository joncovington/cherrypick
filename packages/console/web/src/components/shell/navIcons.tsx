/**
 * The rail's page icons, one per top-level link in `navLinks.ts` — hand-drawn paths on the
 * 24px Lucide-style grid, stroke only, `currentColor` throughout, so a token change recolours
 * them and no icon can drift from its page's own palette. A route with no drawing here falls
 * back to a plain dot — visible in a review, and therefore fixed, rather than silently rendered
 * as some plausible wrong glyph.
 *
 * These are read by the collapsed rail (64px strip: a reader navigating by icon must never have
 * to guess which glyph is which module) and at 14px by the header menu. Tabs stay text-only:
 * ~70 entries of glyphs would be noise, and the rail only ever opens one module at a time.
 */
import type { ReactNode } from "react";

interface NavIconProps {
  size?: number;
}

function Svg({ size = 20, children }: NavIconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/* A soaring condor — the bird on the name (multiple-entry iron condor). Near-flat wings with
   forked primaries, a fan tail and a beak: the three tells that read raptor at rail size and
   keep the glyph from being mistaken for anything else on the rail. */
function MeicIcon() {
  return (
    <Svg>
      <path d="M3 7.5C7 10 9.5 10.8 12 10.8" />
      <path d="M21 7.5C17 10 14.5 10.8 12 10.8" />
      <path d="M3 7.5L4.6 5.6M21 7.5L19.4 5.6" />
      <path d="M10.7 8.8L12 7.4L13.3 8.8" />
      <path d="M12 10.8V14.8" />
      <path d="M12 14.8L9.9 19.2M12 14.8V19.8M12 14.8L14.1 19.2" />
    </Svg>
  );
}

/* The net-credit butterfly: a payoff plateau between two strikes. */
function FliesIcon() {
  return (
    <Svg>
      <path d="M3 17h3l3-7 3 4 3-4 3 7h3" />
      <path d="M9 10l3 4 3-4" opacity="0.45" />
    </Svg>
  );
}

/* A shield with a small short-strike notch — deep-ITM call hedged by a covered short. */
function PmccIcon() {
  return (
    <Svg>
      <path d="M12 3l7 3v5c0 4.4-3 8.2-7 9.4C8 19.2 5 15.4 5 11V6l7-3z" />
      <path d="M12 8v6M9.5 11.5h5" opacity="0.6" />
    </Svg>
  );
}

/* Two curves pulling apart — the VIX term structure the regime gate reads. */
function CurveIcon() {
  return (
    <Svg>
      <path d="M3 16c4 0 6-9 9-9s5 9 9 9" />
      <path d="M3 19c4 0 6-6 9-6s5 6 9 6" opacity="0.45" />
    </Svg>
  );
}

/* Two arrows chasing each other — contango and its roll. */
function ContangoIcon() {
  return (
    <Svg>
      <path d="M4 9h13M14 5.5L17.5 9 14 12.5" />
      <path d="M20 15H7M10 11.5L6.5 15l3.5 3.5" opacity="0.55" />
    </Svg>
  );
}

/* A ladder of long puts stepped downward — the daily put broken-wing butterfly. */
/* A butterfly at rest, side profile — the insect on the name (broken-wing butterfly). One
   wing up, one drooped short: the asymmetry is the broken wing itself, and the pose keeps it
   from being mistaken for Flies' plateau payoff or MEIC's soaring condor. */
function BwbIcon() {
  return (
    <Svg>
      <path d="M12 8C9 5.5 5.5 5 3.5 7C2 8.6 2.6 11.4 5 12.6C7 13.6 9.6 13.2 11 12" />
      <path d="M12 8C14.5 6.5 17.5 7 19 9C20.5 11.2 19 13.8 16.5 14.2C14.6 14.5 12.8 13.6 12 12.2" />
      <path d="M12 8V18.5" />
      <path d="M12 10.5L15.5 18.5" opacity={0.45} />
    </Svg>
  );
}

/* Two overlapping windows — the near and far expirations of a double calendar. */
function CalendarsIcon() {
  return (
    <Svg>
      <rect x="4" y="5" width="13" height="14" rx="1.5" />
      <rect x="8" y="8" width="13" height="14" rx="1.5" opacity="0.55" />
    </Svg>
  );
}

/* A banknote — the cash on the name: defined-risk structures whose point is the credit. */
function EarningsIcon() {
  return (
    <Svg>
      <rect x="3" y="7" width="18" height="10" rx="1.5" />
      <circle cx="12" cy="12" r="2.2" />
      <path d="M6.5 12h.01M17.5 12h.01" />
    </Svg>
  );
}

/* Dealer gamma: long above the flip, short below. */
function GexIcon() {
  return (
    <Svg>
      <path d="M3 17c3.5 0 5-10 9-10s5.5 10 9 10" />
      <path d="M12 7v4" opacity="0.6" />
      <circle cx="12" cy="14" r="1.4" opacity="0.6" />
    </Svg>
  );
}

/* Candles on axes — the charts page. */
function ChartsIcon() {
  return (
    <Svg>
      <path d="M4 4v16h16" />
      <path d="M8 15v-4M12 15V8M16 15v-6" />
    </Svg>
  );
}

/* A funnel over rows — the income screener lists. */
function ScreenersIcon() {
  return (
    <Svg>
      <path d="M4 5h16l-6 7v5l-4 2v-7z" />
    </Svg>
  );
}

/* Flowing tape — the options-flow feed. */
function FlowIcon() {
  return (
    <Svg>
      <path d="M4 8c3 0 3 4 6 4s3-4 6-4 3 2 4 2" />
      <path d="M4 15c3 0 3 3 6 3s3-3 6-3" opacity="0.55" />
    </Svg>
  );
}

/* A filed document — the session reports. */
function ReportsIcon() {
  return (
    <Svg>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M10 12h6M10 16h6" opacity="0.6" />
    </Svg>
  );
}

/* A compass — the advisor's bounded suggestions. */
function AdvisorIcon() {
  return (
    <Svg>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" />
    </Svg>
  );
}

/* An antenna radiating — live trading status. */
function LiveIcon() {
  return (
    <Svg>
      <circle cx="12" cy="12" r="1.6" />
      <path d="M8.5 15.5a5 5 0 010-7M15.5 8.5a5 5 0 010 7" />
      <path d="M5.6 18.4a9 9 0 010-12.8M18.4 5.6a9 9 0 010 12.8" opacity="0.55" />
    </Svg>
  );
}

/* A server rack — the supervisor's services and logs. */
function SystemIcon() {
  return (
    <Svg>
      <rect x="4" y="4" width="16" height="7" rx="1.5" />
      <rect x="4" y="13" width="16" height="7" rx="1.5" />
      <path d="M7.5 7.5h.01M7.5 16.5h.01" />
    </Svg>
  );
}

/* A dial with a keyway — configuration, including the live-trading halt. */
function ConfigIcon() {
  return (
    <Svg>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </Svg>
  );
}

/* The Compare page's own histogram, stood upright: a zero line with a bar rising on one side
   and falling on the other, the pair straddling it — every arm beside its control, and the
   only tone a number's sign. The falling bar carries the sibling-element opacity the rail's
   paired glyphs use (the calendars' second window, contango's second arrow). */
function CompareIcon() {
  return (
    <Svg>
      <path d="M4 12h16" />
      <path d="M9 12V6.5" />
      <path d="M15 17.5V12" opacity="0.55" />
    </Svg>
  );
}

/** By route, not by label — the label is prose and can change; the route is the identity. */
const ICONS: Record<string, (p: NavIconProps) => ReactNode> = {
  "/charts": ChartsIcon,
  "/screeners": ScreenersIcon,
  "/flow": FlowIcon,
  "/reports": ReportsIcon,
  "/compare": CompareIcon,
  "/advisor": AdvisorIcon,
  "/live": LiveIcon,
  "/system": SystemIcon,
  "/meic": MeicIcon,
  "/flies": FliesIcon,
  "/pmcc": PmccIcon,
  "/curve": CurveIcon,
  "/contango": ContangoIcon,
  "/bwb": BwbIcon,
  "/calendars": CalendarsIcon,
  "/earnings": EarningsIcon,
  "/gex": GexIcon,
  "/config": ConfigIcon,
};

/** The icon for a nav link's route, or null where none is drawn yet — a visible gap, not a guess. */
export function NavIcon({ to, size = 20 }: NavIconProps & { to: string }) {
  const Icon = ICONS[to] ?? (({ size = 20 }: NavIconProps) => <Svg size={size}><circle cx="12" cy="12" r="3" /></Svg>);
  return <Icon size={size} />;
}
