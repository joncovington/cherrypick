import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { AXIS_MUTED } from "./Charts";

/**
 * The entry-attempts surfaces: the arm rail and the attempt timeline.
 *
 * Both modules run their arms as independent portfolios with unbounded capital
 * (2026-08-11), so every arm sees the same market with the same money and the
 * only thing separating them is which entries the rules let through. That makes
 * the refusals the primary reading rather than a diagnostic — these two views
 * exist to answer "why is this arm quiet", now and at any past minute of the
 * session, without going to the logs.
 *
 * One component pair serves MEIC and flies. The two modules keep separate
 * ledgers by design (neither may read the other's) but the server normalizes
 * them to one row shape, so the pages do not have to know the difference.
 */

export interface AttemptRow {
  ts: string | null;
  tradeDate: string | null;
  arm: string;
  symbol: string | null;
  outcome: string;
  blockDetail: string | null;
  center: number | null;
  blockingStrike: number | null;
  secondsUntilCadenceClear: number | null;
  spot: number | null;
}

export interface ArmRailEntry {
  arm: string;
  attempts: number;
  fills: number;
  sessionsSeen: number;
  sessionsWithFills: number;
  /** MEIC only — flies has no two-sided structure to stop out twice. */
  resolvedToday: number | null;
  doubleStoppedToday: number | null;
  refusals: Record<string, number>;
  lastRefusal: string | null;
  lastAttemptTs: string | null;
  lastFillTs: string | null;
}

export interface AttemptsPayload {
  mode: TradingMode;
  module: "meic" | "flies";
  tradeDate: string | null;
  breaks: Array<{ arm: string; reason: string }>;
  arms: ArmRailEntry[];
  /** Thinned for transport to the chart's own resolution -- never a source for an exact count.
      See blockedByStrike below. */
  timeline: AttemptRow[];
  /** Exact `sign_rule_blocked` counts per "arm|strike", computed server-side from the untinned
      rows -- what OccupancyMap reads instead of tallying timeline itself. */
  blockedByStrike: Record<string, number>;
}

/**
 * Outcome vocabulary, in the order a refusal is most worth knowing about.
 *
 * `no_fill` is deliberately its own entry and its own colour. Under a
 * fill-based cadence clock an entry that cleared every gate and simply did not
 * fill neither spent the arm's slot nor was refused by a rule — rendering it as
 * a gate refusal would make the gates look stricter than they are.
 */
const OUTCOMES = [
  { key: "filled", label: "filled", color: "#43b57a" },
  { key: "cadence_blocked", label: "cadence", color: "#7aa2ff" },
  { key: "sign_rule_blocked", label: "sign rule", color: "#a06bd9" },
  { key: "duplicate_blocked", label: "duplicate", color: "#c9628a" },
  { key: "gate_blocked", label: "gate", color: "#e88a5c" },
  { key: "window_blocked", label: "window", color: "#8a9c4a" },
  { key: "no_candidate", label: "no candidate", color: "#6c7480" },
  { key: "no_fill", label: "no fill", color: "#d9a13b" },
] as const;

const COLOR_OF: Record<string, string> = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.color]));
const LABEL_OF: Record<string, string> = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.label]));

/** Exported for OccupancyMap, which needs the same day's timeline to derive which strikes
    refused an entry -- sharing this hook (rather than a second near-identical query on the
    same endpoint) means one interval, one in-flight request, one type. */
export function useAttempts(module: "meic" | "flies", mode: TradingMode, date: string | null) {
  return useQuery<AttemptsPayload>({
    queryKey: ["attempts", module, mode, date],
    queryFn: async () => {
      const qs = new URLSearchParams({ mode });
      if (date !== null) qs.set("date", date);
      const res = await fetch(`/api/${module}/attempts?${qs.toString()}`);
      if (!res.ok) throw new Error(`attempts: HTTP ${res.status}`);
      return (await res.json()) as AttemptsPayload;
    },
    refetchInterval: 20_000,
  });
}

/** A ticking wall clock, so the rail's "since" figures advance between polls. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function parseTs(ts: string | null): number | null {
  if (ts === null) return null;
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? null : ms;
}

function fmtGap(seconds: number): string {
  if (seconds < 0) return "0s";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function clockOf(ts: string | null): string {
  const ms = parseTs(ts);
  if (ms === null) return "—";
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * How long until this arm may enter again, derived from the LEDGER rather than
 * from config.
 *
 * The reader has no access to `min_seconds_between_entries`, and inferring a
 * spacing from observed gaps would be a guess wearing a countdown's clothes. So
 * the target instant comes from the arm's most recent cadence refusal, which
 * recorded exactly how many seconds it still had to wait at a known timestamp.
 * Returns null when the arm is not currently cadence-bound, which is the honest
 * answer rather than a zero.
 */
function cadenceRemaining(rows: AttemptRow[], arm: string, now: number): number | null {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const row = rows[i]!;
    if (row.arm !== arm) continue;
    // Only the arm's LATEST attempt speaks to its state right now; anything
    // earlier has been superseded by whatever it did next.
    if (row.outcome !== "cadence_blocked" || row.secondsUntilCadenceClear === null) return null;
    const at = parseTs(row.ts);
    if (at === null) return null;
    return Math.max(row.secondsUntilCadenceClear - (now - at) / 1000, 0);
  }
  return null;
}

/**
 * One card per arm: how far through its cadence it is, what it has taken today,
 * and what is currently holding it back.
 *
 * The single highest-value view of this design — it makes the pacing visible
 * rather than inferred, and turns "that arm has been quiet for an hour" from a
 * thing you notice at EOD into a thing you see at 10:30.
 */
export function ArmRail({
  module,
  mode,
  date = null,
}: {
  module: "meic" | "flies";
  mode: TradingMode;
  date?: string | null;
}) {
  const { data, isLoading } = useAttempts(module, mode, date);
  const arms = data?.arms ?? [];
  const now = useNow(arms.length > 0);

  return (
    <section className="card">
      <div className="panel-head-row">
        <h2>Arms{data?.tradeDate != null ? ` (${data.tradeDate})` : ""}</h2>
        <span className="muted lbl">
          {mode === "paper"
            ? "one portfolio each · unbounded capital · paced by cadence"
            : module === "flies"
              ? "live pilot — real capital, one arm, one position at a time"
              : "live — real capital, under the configured concurrency cap"}
        </span>
      </div>
      {/* Measurement breaks, stated where the numbers are read rather than left in a journal
          nobody opens. A cadence change or an arm added mid-session makes this day non-poolable
          with its neighbours, and a reader comparing arms has no other way to know. */}
      {(data?.breaks ?? []).map((b, i) => (
        <p key={i} className="stale-note" style={{ marginTop: i === 0 ? 0 : "0.3rem" }}>
          <strong>measurement break{b.arm !== "*" ? ` (${b.arm})` : ""}:</strong> {b.reason}
        </p>
      ))}
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "60%" }} />
      ) : arms.length === 0 ? (
        <p className="muted">
          no entry attempts recorded for this day — the loop may not have reached its entry window yet
        </p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))",
            gap: "0.6rem",
          }}
        >
          {arms.map((a) => {
            const remaining = cadenceRemaining(data?.timeline ?? [], a.arm, now);
            const lastFill = parseTs(a.lastFillTs);
            const refusals = Object.entries(a.refusals).sort((x, y) => y[1] - x[1]);
            return (
              <div
                key={a.arm}
                style={{
                  border: "1px solid var(--border, #2a2f38)",
                  borderRadius: 6,
                  padding: "0.55rem 0.65rem",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <strong>{a.arm}</strong>
                  <span className={a.fills > 0 ? "pnl-pos" : "muted"} style={{ fontSize: 12 }}>
                    {a.fills} {a.fills === 1 ? "entry" : "entries"}
                  </span>
                </div>

                <div style={{ fontSize: 12, marginTop: "0.35rem" }}>
                  {remaining !== null ? (
                    <span style={{ color: COLOR_OF["cadence_blocked"] }}>
                      next entry in <strong>{fmtGap(remaining)}</strong>
                    </span>
                  ) : a.lastRefusal !== null ? (
                    <span className="muted">
                      held by <strong>{a.lastRefusal}</strong>
                    </span>
                  ) : (
                    <span className="pnl-pos">eligible</span>
                  )}
                </div>

                {/* Fills per OPPORTUNITY, not fills alone. With every arm on unbounded capital and
                    the same market, this ratio and the rule that consumed the rest IS the
                    experiment: on 2026-08-11 control took 9 of 1,505 and the binding rule was the
                    duplicate check, not the cadence everyone assumed. */}
                <div className="muted" style={{ fontSize: 11, marginTop: "0.25rem" }}>
                  {a.fills} of {a.attempts.toLocaleString()} opportunities
                  {a.attempts > 0 && ` · ${((a.fills / a.attempts) * 100).toFixed(1)}%`}
                </div>

                <div className="muted" style={{ fontSize: 11, marginTop: "0.15rem" }}>
                  {lastFill !== null
                    ? `last fill ${clockOf(a.lastFillTs)} · ${fmtGap((now - lastFill) / 1000)} ago`
                    : "no fill yet today"}
                </div>

                {/* The count that would have stopped a debrief calling a two-position arm a
                    validated thesis. On the card, not in a tooltip. */}
                {a.fills > 0 && a.fills < 5 && (
                  <div style={{ fontSize: 11, marginTop: "0.15rem", color: COLOR_OF["no_fill"] }}>
                    {a.fills} {a.fills === 1 ? "entry" : "entries"} — too few to read
                  </div>
                )}

                {/* An arm evaluating every tick and never filling is the failure this rail exists to
                    catch, and one day cannot show it — today looks identical to "quiet this
                    morning". Escalates to a warning once it has sat out more than one session. */}
                {a.sessionsWithFills === 0 && a.sessionsSeen > 0 && (
                  <div
                    style={{
                      fontSize: 11,
                      marginTop: "0.15rem",
                      color: a.sessionsSeen > 1 ? COLOR_OF["gate_blocked"] : undefined,
                    }}
                    className={a.sessionsSeen > 1 ? undefined : "muted"}
                    title="Enabled and evaluating, but it has never taken an entry. Check the dominant refusal — a gate may be holding it out permanently rather than situationally."
                  >
                    no entries in {a.sessionsSeen} {a.sessionsSeen === 1 ? "session" : "sessions"}
                  </div>
                )}
                {a.sessionsWithFills > 0 && a.sessionsWithFills < a.sessionsSeen && (
                  <div className="muted" style={{ fontSize: 11, marginTop: "0.15rem" }}>
                    traded {a.sessionsWithFills} of {a.sessionsSeen} sessions
                  </div>
                )}

                {/* Double stops: price crossed BOTH short strikes in one session, so both sides were
                    paid to close and nothing was collected. A single-side stop is the design working
                    and averages about -$15; a double averages -$149, and 4.5% of the sample era's
                    trades carry 47% of every stop-related dollar lost. The most direct read on
                    whether an arm's stop policy is working, and it was buried in deep analytics. */}
                {a.doubleStoppedToday !== null && (a.resolvedToday ?? 0) > 0 && (
                  <div
                    style={{ fontSize: 11, marginTop: "0.15rem" }}
                    className={a.doubleStoppedToday === 0 ? "muted" : undefined}
                    title="Both short sides stopped in the same session — the whipsaw case, and the only outcome that can lose multiples of the credit."
                  >
                    <span style={a.doubleStoppedToday > 0 ? { color: COLOR_OF["gate_blocked"] } : undefined}>
                      {a.doubleStoppedToday} double-stopped
                    </span>{" "}
                    of {a.resolvedToday} resolved
                    {a.doubleStoppedToday > 0 &&
                      ` · ${((a.doubleStoppedToday / (a.resolvedToday || 1)) * 100).toFixed(1)}%`}
                  </div>
                )}

                {/* Refusals as a proportion bar: which rule is actually doing the
                    work on this arm today, at a glance and without a table. */}
                {refusals.length > 0 && (
                  <>
                    <div style={{ display: "flex", height: 5, marginTop: "0.4rem", borderRadius: 3, overflow: "hidden" }}>
                      {refusals.map(([outcome, n]) => (
                        <span
                          key={outcome}
                          title={`${LABEL_OF[outcome] ?? outcome}: ${n}`}
                          style={{
                            width: `${(n / (a.attempts - a.fills || 1)) * 100}%`,
                            background: COLOR_OF[outcome] ?? "#6c7480",
                          }}
                        />
                      ))}
                    </div>
                    <div className="muted" style={{ fontSize: 11, marginTop: "0.3rem" }}>
                      {refusals
                        .slice(0, 3)
                        .map(([outcome, n]) => `${LABEL_OF[outcome] ?? outcome} ${n}`)
                        .join(" · ")}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

const LANE_H = 22;
const SESSION_START_MIN = 9 * 60 + 30;
const SESSION_END_MIN = 16 * 60;

function minuteOfDay(ts: string | null): number | null {
  const ms = parseTs(ts);
  if (ms === null) return null;
  const d = new Date(ms);
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

/**
 * One lane per arm across the session, every evaluated entry marked by outcome.
 *
 * When an arm goes quiet this says immediately whether it was the cadence, the
 * sign rule, or a gate — the diagnosis that was missing on the day a MEIC arm
 * took zero trades and nothing recorded which gate had refused each tick.
 *
 * Drawn against the whole RTH session rather than only the range that has data,
 * so a lane that stops half way reads as an arm that stopped rather than as the
 * chart running out — the same reason the flies timeline breaks its lines
 * across gaps instead of interpolating.
 */
export function AttemptTimeline({
  module,
  mode,
  date = null,
}: {
  module: "meic" | "flies";
  mode: TradingMode;
  date?: string | null;
}) {
  const { data, isLoading } = useAttempts(module, mode, date);
  const [hover, setHover] = useState<AttemptRow | null>(null);
  const rows = data?.timeline ?? [];
  const arms = (data?.arms ?? []).map((a) => a.arm);

  const width = 1150;
  const pad = { l: 92, r: 12, t: 18, b: 24 };
  const height = pad.t + pad.b + Math.max(arms.length, 1) * LANE_H;
  const X = (min: number) =>
    pad.l + ((min - SESSION_START_MIN) / (SESSION_END_MIN - SESSION_START_MIN)) * (width - pad.l - pad.r);

  // Hour gridlines, plus the 6-minute ticks the cadence is measured in — light
  // enough to read as texture rather than as data.
  const hourTicks: number[] = [];
  for (let m = SESSION_START_MIN; m <= SESSION_END_MIN; m += 60) hourTicks.push(m);

  return (
    <section className="card">
      <div className="panel-head-row">
        <h2>Entry attempts{data?.tradeDate != null ? ` (${data.tradeDate})` : ""}</h2>
        <span className="muted lbl">every evaluated opportunity, filled or refused</span>
      </div>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "60%" }} />
      ) : rows.length === 0 ? (
        <p className="muted">nothing recorded for this day</p>
      ) : (
        <>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label="entry attempt timeline"
            style={{ width: "100%", height: "auto", display: "block" }}
            onMouseLeave={() => setHover(null)}
          >
            {hourTicks.map((m) => (
              <g key={m}>
                <line x1={X(m)} x2={X(m)} y1={pad.t - 4} y2={height - pad.b} stroke="#2a2f38" strokeWidth={1} />
                <text x={X(m)} y={height - pad.b + 14} fontSize={10} fill={AXIS_MUTED} textAnchor="middle">
                  {`${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`}
                </text>
              </g>
            ))}
            {arms.map((arm, i) => {
              const y = pad.t + i * LANE_H;
              return (
                <g key={arm}>
                  <text x={pad.l - 8} y={y + LANE_H / 2 + 3} fontSize={11} fill={AXIS_MUTED} textAnchor="end">
                    {arm}
                  </text>
                  <line
                    x1={pad.l}
                    x2={width - pad.r}
                    y1={y + LANE_H / 2}
                    y2={y + LANE_H / 2}
                    stroke="#1e232b"
                    strokeWidth={1}
                  />
                  {rows
                    .filter((r) => r.arm === arm)
                    .map((r, j) => {
                      const min = minuteOfDay(r.ts);
                      if (min === null) return null;
                      const filled = r.outcome === "filled";
                      return (
                        <rect
                          key={`${arm}-${j}`}
                          x={X(min) - (filled ? 2 : 1)}
                          y={y + (filled ? 3 : 7)}
                          width={filled ? 4 : 2}
                          height={filled ? LANE_H - 6 : LANE_H - 14}
                          fill={COLOR_OF[r.outcome] ?? "#6c7480"}
                          opacity={filled ? 1 : 0.75}
                          onMouseEnter={() => setHover(r)}
                        />
                      );
                    })}
                </g>
              );
            })}
          </svg>

          <div className="legend-row" style={{ marginTop: "0.4rem" }}>
            {OUTCOMES.filter((o) => rows.some((r) => r.outcome === o.key)).map((o) => (
              <span key={o.key} className="muted" style={{ fontSize: 11, marginRight: "0.8rem" }}>
                <i style={{ background: o.color, display: "inline-block", width: 8, height: 8, marginRight: 4 }} />
                {o.label}
              </span>
            ))}
          </div>

          <p className="muted" style={{ fontSize: 12, minHeight: "1.2em", margin: "0.35rem 0 0" }}>
            {hover !== null
              ? `${clockOf(hover.ts)} ${hover.arm} — ${LABEL_OF[hover.outcome] ?? hover.outcome}` +
                (hover.blockDetail !== null ? ` (${hover.blockDetail})` : "") +
                (hover.blockingStrike !== null ? ` at ${hover.blockingStrike}` : "") +
                (hover.secondsUntilCadenceClear !== null
                  ? `, ${Math.round(hover.secondsUntilCadenceClear)}s still to wait`
                  : "") +
                (hover.spot !== null ? ` · spot ${hover.spot.toFixed(2)}` : "")
              : "hover a mark for the reason"}
          </p>
        </>
      )}
    </section>
  );
}
