import { useQuery } from "@tanstack/react-query";
import type { ManageBand, ManageBandsHistoryPayload, ManageBandsSession } from "@console/shared";
import { Card } from "../../components/DataTable";

/**
 * The bands over time: each stored evaluation's delta, theta and buying-power reading against the
 * band that day's evaluation judged it by. A band moves with the book (the delta band is scaled by
 * theta, the buying-power cap by the VIX), so each session draws its own bounds; nothing here works
 * out a band or an "inside". Out-of-band points are amber, as on the dials above.
 */
export function useManageBands() {
  return useQuery<ManageBandsHistoryPayload>({
    queryKey: ["manage", "bands"],
    queryFn: async () => {
      const res = await fetch("/api/manage/bands");
      if (!res.ok) throw new Error(`manage bands: HTTP ${res.status}`);
      return (await res.json()) as ManageBandsHistoryPayload;
    },
    // A new point lands at most every 30 minutes, and only the session's last run is stored.
    refetchInterval: 5 * 60_000,
  });
}

type BandOf = (s: ManageBandsSession) => ManageBand | null;

interface Strip {
  label: string;
  pick: BandOf;
  fmt: (v: number) => string;
}

const STRIPS: Strip[] = [
  { label: "beta-weighted delta", pick: (s) => s.delta, fmt: (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}` },
  { label: "theta, % of net liq per day", pick: (s) => s.thetaPct, fmt: (v) => `${(v * 100).toFixed(2)}%` },
  { label: "buying power used", pick: (s) => s.buyingPower, fmt: (v) => `${(v * 100).toFixed(0)}%` },
];

/** The lightbox tabs' names for the strips, in STRIPS order. */
const STRIP_KEYS = ["delta", "theta", "bp"];

const W = 320;
const H = 64;
const PAD = 6;

/** "10-30" from "2026-10-30". */
function day(session: string): string {
  return session.slice(5);
}

function BandStrip({ sessions, strip }: { sessions: ManageBandsSession[]; strip: Strip }) {
  const rows = sessions
    .map((s) => ({ session: s.session, band: strip.pick(s) }))
    .filter((r): r is { session: string; band: ManageBand } => r.band !== null);
  const placed = rows.filter((r) => r.band.value !== null && r.band.inside !== null);
  if (rows.length < 2) return null;
  const nums = rows.flatMap((r) => [r.band.low, r.band.high, r.band.value]).filter((v): v is number => v !== null);
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const span = hi - lo || 1;
  const x = (i: number) => PAD + (i / (rows.length - 1)) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - ((v - lo) / span) * (H - 2 * PAD);
  // The band as one shape: the highs left to right, then the lows back, each held flat across its
  // session's half-step so a change of bound reads as a step, not as a slope between two days.
  const half = (W - 2 * PAD) / (rows.length - 1) / 2;
  const edge = (i: number, v: number): Array<[number, number]> => [
    [Math.max(PAD, x(i) - half), y(v)],
    [Math.min(W - PAD, x(i) + half), y(v)],
  ];
  const bounded = rows.map((r, i) => ({ i, low: r.band.low, high: r.band.high }));
  const tops = bounded.flatMap((b) => (b.high === null ? [] : edge(b.i, b.high)));
  const bottoms = [...bounded].reverse().flatMap((b) => (b.low === null ? [] : edge(b.i, b.low).reverse()));
  const area = [...tops, ...bottoms].map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const line = rows
    .map((r, i) => (r.band.value === null ? null : `${x(i).toFixed(1)},${y(r.band.value).toFixed(1)}`))
    .filter((p): p is string => p !== null)
    .join(" ");
  const inside = placed.filter((r) => r.band.inside).length;
  const last = rows[rows.length - 1];
  return (
    <div className="bh-strip">
      <div className="bh-head">
        <span>{strip.label}</span>
        <span className="muted">
          {last?.band.value == null ? "—" : strip.fmt(last.band.value)} · inside {inside} of {placed.length} sessions
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${strip.label} against its band`}>
        {tops.length > 0 && bottoms.length > 0 && <polygon points={area} className="bh-band" />}
        <polyline points={line} className="bh-line" fill="none" />
        {rows.map((r, i) =>
          r.band.value === null ? null : (
            <circle
              key={r.session}
              cx={x(i)}
              cy={y(r.band.value)}
              r={2.6}
              className={r.band.inside === false ? "bh-dot bh-out" : "bh-dot"}
              data-out={r.band.inside === false ? "1" : "0"}
            >
              <title>
                {`${r.session}: ${strip.fmt(r.band.value)}, band ${r.band.low === null ? "—" : strip.fmt(r.band.low)} to ${
                  r.band.high === null ? "—" : strip.fmt(r.band.high)
                }${r.band.inside === false ? " (outside)" : ""}`}
              </title>
            </circle>
          ),
        )}
      </svg>
    </div>
  );
}

/** The strips and their caption, a pure render of one account's sessions; `only` picks one strip
 *  ("delta", "theta" or "bp") drawn at full width. */
export function BandsHistoryBody({ sessions, only = null }: { sessions: ManageBandsSession[]; only?: string | null }) {
  const first = sessions[0];
  const last = sessions[sessions.length - 1];
  if (!first || !last || sessions.length < 2) return <p className="muted">needs two evaluations with bands; {sessions.length} so far</p>;
  const strips = STRIPS.filter((_, i) => only === null || STRIP_KEYS[i] === only);
  return (
    <>
      <div className={only === null ? "bh-grid" : "bh-grid bh-one"}>
        {strips.map((s) => (
          <BandStrip key={s.label} sessions={sessions} strip={s} />
        ))}
      </div>
      <p className="muted pf-caption">
        {day(first.session)} to {day(last.session)}, one point per session (its last
        evaluation) · the shaded band is that day's own, as manage judged it · amber: outside it
      </p>
    </>
  );
}

/** The card, as the tab drew it before the lightbox (kept for the tests and any other page). */
export function BandsHistoryView({ sessions }: { sessions: ManageBandsSession[] }) {
  return (
    <Card title="bands over time">
      <BandsHistoryBody sessions={sessions} />
    </Card>
  );
}

/** The portfolio tab's "over time" lightbox: one account's strips, fetched when it opens. */
export function BandsOverTime({ account, only = null }: { account: string | null; only?: string | null }) {
  const q = useManageBands();
  const found = q.data?.accounts.find((a) => a.account === account) ?? null;
  if (q.isLoading) return <p className="muted">Loading the bands…</p>;
  if (q.error) return <p className="muted">bands over time: {(q.error as Error).message}</p>;
  return <BandsHistoryBody sessions={found?.sessions ?? []} only={only} />;
}
