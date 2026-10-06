import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  AgentArmDay,
  AgentCheck,
  AgentCriterion,
  AgentTaggedClose,
  FliesAgentPack,
  FliesAgentPayload,
  TradingMode,
} from "@console/shared";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { ARM_COLORS, AXIS_FONT_FAMILY, AXIS_MUTED_HEX, SPOT_COLOR } from "../../components/chart/tokens";
import { niceTicks } from "../../components/chart/scales";
import { hhmm, minuteOf } from "../../components/chart/time";
import { fmtCash, fmtPct, fmtPrice } from "../../lib/format";
import { Tile } from "./PerformanceTab";
import { useTimeline } from "./TimelineCard";

/**
 * The intraday agent (packages/flies/docs/intraday-agent-plan.md): what it decided today, how its
 * paper arm stands against `trend-rule` and `control`, and what the evidence lets live offer.
 *
 * Nothing on this slide judges. Pass/fail, the paired bound, the offered modes and every settled
 * figure are the module's qualification file (`intraday_eval`); "following the agent" is the arm's
 * own rule for a fresh decision. Closes are TAGS: a vertical the rule or the agent would have closed,
 * priced at natural when tagged and left to settle, so "net if closed" is a valuation, never a fill.
 */

const GATE_ON = "#d9a13b";
const GATE_OFF = "#7aa2ff";
const GATE_BAD = "#82878f";

function useAgent(session: string | null) {
  return useQuery<FliesAgentPayload>({
    queryKey: ["flies-agent", session],
    queryFn: async () => {
      const res = await fetch(`/api/flies/agent${session !== null ? `?session=${session}` : ""}`);
      if (!res.ok) throw new Error(`flies agent: HTTP ${res.status}`);
      return (await res.json()) as FliesAgentPayload;
    },
    refetchInterval: 30_000,
  });
}

const clock = (ts: string | null): string => (ts === null ? "—" : ts.slice(11, 16));
const usd = (v: number | null): string => (v === null ? "—" : `$${v.toFixed(2)}`);
const structure = (side: string | null, center: number | null, width: number | null): string =>
  `${side ?? "?"} ${center !== null ? center.toFixed(0) : "?"}${width !== null ? ` · ${width.toFixed(0)}w` : ""}`;

function ageLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  const mins = Math.floor(seconds / 60);
  return mins < 60 ? `${String(mins)}m ago` : `${String(Math.floor(mins / 60))}h ${String(mins % 60)}m ago`;
}

// --------------------------------------------------------------------------- the day's chart
function DecisionChart({ data }: { data: FliesAgentPayload }) {
  const session = data.session;
  const timeline = useTimeline("paper", { arm: null, date: session, symbol: null, era: null });
  const agentArm = data.qualification?.arms.agent ?? data.config?.paperArm ?? "intraday-agent";
  const band = data.qualification?.trendBandPoints ?? null;
  const ticks = (timeline.data?.ticks ?? []).filter((t) => t.spot !== null);
  const events = (timeline.data?.events ?? []).filter((e) => e.arm === agentArm && e.spot !== null);
  const called = data.checks.filter((c) => c.called && c.target === "paper");
  const tags = [
    ...data.openTags.map((t) => ({ at: t.taggedAt, spot: t.spot, arm: t.arm, label: structure(t.side, t.center, t.wingWidth) })),
    ...(data.qualification?.taggedCloses ?? [])
      .filter((t) => t.tradeDate === session)
      .map((t) => ({ at: t.taggedAt, spot: t.spot, arm: t.arm, label: structure(t.side, t.center, t.wingWidth) })),
  ].filter((t): t is { at: string; spot: number; arm: string; label: string } => t.at !== null && t.spot !== null);

  if (ticks.length === 0) {
    return <p className="muted">{timeline.isLoading ? "reading…" : "no spot recorded for this session"}</p>;
  }
  const width = 1150;
  const height = 260;
  const pad = { l: 48, r: 10, t: 10, b: 22 };
  const mins = ticks.map((t) => minuteOf(t.ts));
  const tMin = Math.floor(Math.min(mins[0]!, 9 * 60 + 30) / 30) * 30;
  const tMax = 16 * 60;
  const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin || 1)) * (width - pad.l - pad.r);
  const levels = [
    ...ticks.map((t) => t.spot!),
    ...(data.dayOpen !== null && band !== null ? [data.dayOpen - band, data.dayOpen + band] : []),
  ];
  const lo = Math.min(...levels);
  const hi = Math.max(...levels);
  const span = hi - lo || 1;
  const yMin = lo - span * 0.06;
  const yMax = hi + span * 0.06;
  const Y = (v: number) => height - pad.b - ((v - yMin) / (yMax - yMin)) * (height - pad.b - pad.t);
  // The timeline's gap rule: a step over three times the median cadence breaks the line.
  const steps = mins.slice(1).map((m, i) => m - mins[i]!).filter((d) => d > 0).sort((a, b) => a - b);
  const gapLimit = Math.max((steps[Math.floor(steps.length / 2)] ?? 0) * 3, 5);
  const segs: string[] = [];
  let cur: string[] = [];
  ticks.forEach((t, i) => {
    if (i > 0 && mins[i]! - mins[i - 1]! > gapLimit && cur.length > 0) {
      segs.push(cur.join(" "));
      cur = [];
    }
    cur.push(`${X(mins[i]!).toFixed(1)},${Y(t.spot!).toFixed(1)}`);
  });
  if (cur.length > 1) segs.push(cur.join(" "));
  const hours: number[] = [];
  for (let m = Math.ceil(tMin / 60) * 60; m <= tMax; m += 60) hours.push(m);
  const markerY = pad.t + 4;

  return (
    <svg viewBox={`0 0 ${String(width)} ${String(height)}`} width="100%" role="img" aria-label="spot path with the agent's decisions">
      {niceTicks(yMin, yMax, 4).map((v) => (
        <g key={v}>
          <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke="#15181e" />
          <text x={4} y={Y(v) + 3} fontSize={9} fill={AXIS_MUTED_HEX} fontFamily={AXIS_FONT_FAMILY}>{v.toFixed(0)}</text>
        </g>
      ))}
      {hours.map((m) => (
        <text key={m} x={X(m)} y={height - 6} fontSize={9} fill={AXIS_MUTED_HEX} textAnchor="middle" fontFamily={AXIS_FONT_FAMILY}>
          {hhmm(m)}
        </text>
      ))}
      {data.dayOpen !== null && (
        <g>
          <line x1={pad.l} x2={width - pad.r} y1={Y(data.dayOpen)} y2={Y(data.dayOpen)} stroke={AXIS_MUTED_HEX} strokeDasharray="2 3" />
          {band !== null &&
            [data.dayOpen - band, data.dayOpen + band].map((v) => (
              <line key={v} x1={pad.l} x2={width - pad.r} y1={Y(v)} y2={Y(v)} stroke={AXIS_MUTED_HEX} strokeDasharray="6 4" opacity={0.7} />
            ))}
        </g>
      )}
      {segs.map((s, k) => (
        <polyline key={k} points={s} fill="none" stroke={SPOT_COLOR} strokeWidth={1.4} />
      ))}
      {called.map((c) => {
        const x = X(minuteOf(c.at));
        const color = c.ok !== true ? GATE_BAD : c.trendGate === "on" ? GATE_ON : GATE_OFF;
        return (
          <g key={c.index}>
            <line x1={x} x2={x} y1={pad.t} y2={height - pad.b} stroke={color} opacity={0.18} />
            <rect x={x - 3} y={markerY - 3} width={6} height={6} fill={color}>
              <title>{`${clock(c.at)} · ${c.ok === true ? `gate ${c.trendGate ?? "?"}` : `not admissible: ${c.error ?? "?"}`}${c.closeLabels.length > 0 ? ` · close ${c.closeLabels.join(", ")}` : ""}${c.reason !== null ? ` — ${c.reason}` : ""}`}</title>
            </rect>
          </g>
        );
      })}
      {data.refusals
        .filter((r) => r.arm === agentArm && r.spot !== null)
        .map((r, k) => {
          const x = X(minuteOf(r.at));
          const y = Y(r.spot!);
          return (
            <path key={`r${String(k)}`} d={`M${x - 4} ${y - 4}L${x + 4} ${y + 4}M${x + 4} ${y - 4}L${x - 4} ${y + 4}`} stroke={GATE_ON} strokeWidth={1.4}>
              <title>{`${clock(r.at)} entry refused by the trend gate${r.center !== null ? ` · centre ${r.center.toFixed(0)}` : ""}`}</title>
            </path>
          );
        })}
      {events.map((e, k) => {
        const x = X(minuteOf(e.ts));
        const y = Y(e.spot!);
        const title = `${clock(e.ts)} ${e.kind}${e.center !== null ? ` · centre ${e.center.toFixed(0)}` : ""}`;
        return e.kind === "entry" ? (
          <circle key={`e${String(k)}`} cx={x} cy={y} r={3.5} fill="none" stroke={ARM_COLORS[0]} strokeWidth={1.5}>
            <title>{title}</title>
          </circle>
        ) : (
          <path key={`e${String(k)}`} d={`M${x} ${y - 4.5}L${x + 4.5} ${y}L${x} ${y + 4.5}L${x - 4.5} ${y}Z`} fill={ARM_COLORS[1]}>
            <title>{title}</title>
          </path>
        );
      })}
      {tags.map((t, k) => {
        const x = X(minuteOf(t.at));
        const y = Y(t.spot);
        return (
          <path key={`t${String(k)}`} d={`M${x} ${y - 6}L${x + 5} ${y + 3}L${x - 5} ${y + 3}Z`} fill="none" stroke={ARM_COLORS[7]} strokeWidth={1.5}>
            <title>{`${clock(t.at)} close tagged · ${t.arm} · ${t.label}`}</title>
          </path>
        );
      })}
    </svg>
  );
}

// --------------------------------------------------------------------------- tables
function ArmNet({ day }: { day: AgentArmDay | null }) {
  return day === null ? <span className="muted">—</span> : <PnlCell v={day.settledNet} />;
}

function strands(day: AgentArmDay | null): string {
  return day === null ? "—" : `${String(day.stranded)}/${String(day.entries)}`;
}

function criterionValue(c: AgentCriterion): string {
  if (c.value === null) return "—";
  if (c.id === "beats_rule" || c.id === "closes_survive_2x") return fmtCash(c.value);
  if (c.id === "strands_no_more") return fmtPct(c.value * 100, 1);
  return String(c.value);
}

function criterionThreshold(c: AgentCriterion): string {
  if (c.threshold === null) return "—";
  if (c.id === "beats_rule" || c.id === "closes_survive_2x" || c.id === "shadow_closes_live") return `> ${fmtCash(c.threshold)}`;
  if (c.id === "strands_no_more") return `≤ ${fmtPct(c.threshold * 100, 1)} (rule)`;
  return `≥ ${String(c.threshold)}`;
}

function PassCell({ pass }: { pass: boolean | null }) {
  if (pass === null) return <span className="muted" title="the suite cannot score this yet; it never unlocks">not scored</span>;
  return <span className={pass ? "pnl-pos" : "pnl-neg"}>{pass ? "pass" : "not yet"}</span>;
}

function PackRow({ session, check, cols }: { session: string; check: AgentCheck; cols: number }) {
  const pack = useQuery<FliesAgentPack>({
    queryKey: ["flies-agent-pack", session, check.index],
    queryFn: async () => {
      const res = await fetch(`/api/flies/agent/pack?session=${session}&index=${String(check.index)}`);
      if (!res.ok) throw new Error(`pack: HTTP ${res.status}`);
      return (await res.json()) as FliesAgentPack;
    },
    staleTime: Infinity,
  });
  return (
    <tr>
      <td colSpan={cols}>
        <pre className="spec-block" style={{ maxHeight: 360, overflow: "auto" }}>
          {pack.data !== undefined ? JSON.stringify(pack.data.pack, null, 2) : pack.isError ? "pack not readable" : "reading…"}
        </pre>
      </td>
    </tr>
  );
}

function closeRow(c: AgentTaggedClose) {
  return (
    <tr key={`${c.arm}:${c.positionId}`}>
      <td>{c.tradeDate}</td>
      <td>{c.arm}</td>
      <td>{c.source ?? "—"}</td>
      <td>{clock(c.taggedAt)}</td>
      <td>{structure(c.side, c.center, c.wingWidth)}</td>
      <td>{c.spot !== null ? c.spot.toFixed(2) : "—"}</td>
      <td>{fmtPrice(c.credit)}</td>
      <td>{fmtPrice(c.natural !== null ? -c.natural : null)}</td>
      <td>{fmtPrice(c.mid !== null ? -c.mid : null)}</td>
      <td>{fmtCash(c.fees !== null ? -c.fees : null)}</td>
      <td><PnlCell v={c.settledNet} /></td>
      <td><PnlCell v={c.closedNet} /></td>
      <td><PnlCell v={c.closedNet2x} /></td>
      <td><PnlCell v={c.saved} /></td>
    </tr>
  );
}

// --------------------------------------------------------------------------- the slide
export function AgentSlide({ mode, date }: { mode: TradingMode; date: string | null }) {
  const { data, isLoading, isError, dataUpdatedAt } = useAgent(date);
  const [showSkips, setShowSkips] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  // Paper-only until the live shadow is scored (step 4): refuse a live read rather than show the
  // paper arm under a live badge.
  if (mode === "live") {
    return (
      <div className="cards cards-wide">
        <section className="card">
          <p className="muted">
            This tab reads the paper arm and the module's qualification file, so it has no live view. The live shadow (chosen
            per day at /live-flies-start) is scored there and shown under &quot;live shadow&quot; in the Paper view.
          </p>
        </section>
      </div>
    );
  }

  if (data === undefined) {
    return (
      <div className="cards cards-wide">
        <section className="card">
          <p className="muted">{isLoading ? "reading…" : isError ? "the agent payload could not be read" : "—"}</p>
        </section>
      </div>
    );
  }

  const q = data.qualification;
  const d = data.decision;
  const cfg = data.config;
  const paperChecks = data.checks.filter((c) => c.target === "paper");
  const calls = paperChecks.filter((c) => c.called);
  const decisionValue = d?.fresh === true ? `gate ${d.trendGate ?? "?"}` : "rule";
  const rows = showSkips ? paperChecks : calls;
  const sessionCloses = q?.taggedCloses ?? [];
  const perSession = [...(q?.perSession ?? [])].reverse();
  const spend = [...(q?.spend ?? [])].reverse();
  const decided = q?.sessions.decided ?? null;
  const minSessions = q?.criteria.find((c) => c.id === "decision_sessions")?.threshold ?? null;

  return (
    <div className="cards cards-wide">
      <section className="card">
        <TileGrid count={6}>
          <Tile
            label="following now"
            value={decisionValue}
            tone={d?.fresh === true ? undefined : "dim"}
            title={
              d === null
                ? "no decision file yet: the arm runs the fixed rule"
                : d.fresh
                  ? `${d.model ?? "?"} at ${clock(d.at)}, expires ${clock(d.expiresAt)}${d.reason !== null ? ` — ${d.reason}` : ""}`
                  : "no fresh decision for this session: the arm runs trend-rule's gate exactly"
            }
          />
          <Tile label="last decision" value={d !== null && d.session === data.session ? `${clock(d.at)} · ${ageLabel(d.ageSeconds)}` : "—"} />
          <Tile
            label="calls this session"
            value={`${String(calls.length)}${cfg?.maxCallsPerSession != null ? ` / ${String(cfg.maxCallsPerSession)}` : ""}`}
            title={`${String(paperChecks.length)} checks; a call is made only near the trend band or with a vertical open`}
          />
          <Tile label="spend this session" value={usd(data.sessionSpend.calls > 0 ? data.sessionSpend.costUsd : null)} tone="dim" />
          <Tile
            label="sessions decided"
            value={decided === null ? "—" : `${String(decided)}${minSessions !== null ? ` / ${String(minSessions)}` : ""}`}
            title="sessions with an admissible paper decision, against the gates criterion"
          />
          <Tile
            label="live may offer"
            value={q !== null ? q.offeredModes.join(" · ") : "—"}
            title={`capped by live_mode_max (${q?.liveModeMax ?? cfg?.liveModeMax ?? "?"}) and by the criteria below`}
          />
        </TileGrid>
        {cfg !== null && !cfg.enabled && <p className="muted">The agent is off in flies.json (intraday_agent.enabled); the arm runs the fixed rule.</p>}
      </section>

      <Card title={`the session${data.session !== null ? ` · ${data.session}` : ""}`} updatedAt={dataUpdatedAt}>
        {data.session !== null ? (
          <DecisionChart data={data} />
        ) : (
          <p className="muted">No agent session recorded yet: the job's first check runs at 09:30 on a session day.</p>
        )}
        <p className="muted">
          ■ a model call (amber gate on, blue gate off, grey not admissible) · ✕ an entry the gate refused · ○ ◆ the agent arm's
          entries and completions · △ a close tagged · dashed: the open and ±{q?.trendBandPoints ?? "?"} points, the gate's band
          {data.dayOpen === null ? " (drawn once a call has seen the open)" : ""}
        </p>
      </Card>

      <DataCard
        title="decisions"
        headers={["time", "check", "model", "cost", "secs", "gate", "closes", "conf.", "reason", ""]}
        loading={false}
        rowCount={rows.length}
        empty={paperChecks.length === 0 ? "no checks recorded for this session" : "no model calls this session"}
        controls={
          <label className="muted">
            <input type="checkbox" checked={showSkips} onChange={(e) => setShowSkips(e.target.checked)} /> show skipped checks
          </label>
        }
        footer="Every check is recorded with its pack, called or not: the material a deterministic rule is later fitted from. The pack is what the model saw."
      >
        {rows.map((c) => (
          <Fragment key={c.index}>
            <tr>
              <td>{clock(c.at)}</td>
              <td>{c.called ? (c.trigger ?? "called") : <span className="muted">{c.skipped ?? "skipped"}</span>}</td>
              <td>{c.model ?? "—"}</td>
              <td>{usd(c.costUsd)}</td>
              <td>{c.seconds !== null ? c.seconds.toFixed(0) : "—"}</td>
              <td>{c.called ? (c.ok === true ? (c.trendGate ?? "—") : <span className="pnl-neg" title={c.error ?? ""}>not admissible</span>) : "—"}</td>
              <td>
                {c.closeLabels.join(", ") || "—"}
                {c.droppedCloses.length > 0 && <span className="muted" title="past the wing: dropped by the validator"> (dropped {c.droppedCloses.join(", ")})</span>}
              </td>
              <td>{c.confidence !== null ? c.confidence.toFixed(2) : "—"}</td>
              <td>{c.reason ?? (c.error !== null ? <span className="muted">{c.error}</span> : "—")}</td>
              <td>
                {c.called && data.session !== null && (
                  <button type="button" className="link-button" onClick={() => setOpen(open === c.index ? null : c.index)}>
                    {open === c.index ? "hide pack" : "pack"}
                  </button>
                )}
              </td>
            </tr>
            {open === c.index && data.session !== null && <PackRow session={data.session} check={c} cols={10} />}
          </Fragment>
        ))}
      </DataCard>

      <DataCard
        title="arms by session"
        headers={["session", "source", "decided", "control", "trend-rule", "agent", "agent − rule", "rule strands", "agent strands", "agent if closed (2x)"]}
        loading={false}
        rowCount={perSession.length}
        empty={data.qualificationStatus === "absent" ? "not evaluated yet: `run.py agent-eval --write`, or the next settlement" : "no settled session for the rule or agent arm yet"}
        numFrom={3}
        footer="Settled net, after fees. Strands are uncompleted verticals over entries. The last column values the agent arm's tagged closes at natural plus one more spread's worth. Replay rows are control's recorded entries less those each gate refuses; past sessions kept no quotes, so they value no close."
      >
        {perSession.map((r) => (
          <tr key={r.session}>
            <td>{r.session}</td>
            <td>{r.source === "replay" ? <span className="muted" title="historical replay: the gate only, no closes valued">replay</span> : "forward"}</td>
            <td>{r.decided ? "yes" : <span className="muted">no</span>}</td>
            <td><ArmNet day={r.control} /></td>
            <td><ArmNet day={r.rule} /></td>
            <td><ArmNet day={r.agent} /></td>
            <td><PnlCell v={r.agentLessRule} /></td>
            <td>{strands(r.rule)}</td>
            <td>{strands(r.agent)}</td>
            <td>{r.agent !== null ? <PnlCell v={r.agent.netCloses2x} /> : <span className="muted">—</span>}</td>
          </tr>
        ))}
      </DataCard>

      <DataCard
        title="what live may offer"
        headers={["unlocks", "criterion", "value", "needs", ""]}
        loading={false}
        rowCount={q?.criteria.length ?? 0}
        empty={data.qualificationStatus === "failed" ? "the qualification file could not be read" : "not evaluated yet"}
        footer={
          q === null
            ? undefined
            : `Paired over ${String(q.gates.n)} sessions: mean ${fmtCash(q.gates.mean)}, one-sided 95% bound ${fmtCash(q.gates.lower95)}` +
              `${q.gates.sessionsNeeded !== null ? `, about ${String(q.gates.sessionsNeeded)} sessions to detect ${fmtCash(q.gates.targetEdge)} at this spread` : ""}` +
              ` · evaluated ${q.generatedAt ?? "—"} by intraday_eval; "not scored" never unlocks.`
        }
      >
        {(q?.criteria ?? []).map((c) => (
          <tr key={c.id}>
            <td>{c.mode === "gates" ? "gates" : "gates + closures"}</td>
            <td>{c.label}</td>
            <td>{criterionValue(c)}</td>
            <td>{criterionThreshold(c)}</td>
            <td><PassCell pass={c.pass} /></td>
          </tr>
        ))}
      </DataCard>

      <DataCard
        title="live shadow"
        headers={["session", "entries", "gate would refuse", "live effect", "paper effect", "counted", "agree"]}
        loading={false}
        rowCount={q?.shadow.sessions.length ?? 0}
        empty="no live session run with the agent in shadow yet"
        numFrom={1}
        footer={
          q === null
            ? undefined
            : `Live effect: what refusing the entries the agent's gate would have refused was worth (minus their settled net). Paper effect: agent arm less control, same session. ` +
              `${String(q.shadow.agree)} of ${String(q.shadow.counted)} counted sessions agree in sign` +
              ` · shadow closes tagged at live natural: ${String(q.shadow.closes.tagged)}, saved ${fmtCash(q.shadow.closes.saved)}` +
              (q.liveClosesBuilt === false ? " · live closing orders are not built, so closures are never offered" : "")
        }
      >
        {[...(q?.shadow.sessions ?? [])].reverse().map((r) => (
          <tr key={r.session}>
            <td>{r.session}</td>
            <td>{r.entries}</td>
            <td>{r.wouldRefuse}</td>
            <td><PnlCell v={r.liveEffect} /></td>
            <td><PnlCell v={r.paperEffect} /></td>
            <td>{r.counted ? "yes" : <span className="muted">no effect</span>}</td>
            <td>{r.agree === null ? <span className="muted">—</span> : r.agree ? "yes" : "no"}</td>
          </tr>
        ))}
      </DataCard>

      <DataCard
        title="tagged closes"
        headers={["session", "arm", "by", "tagged", "vertical", "spot", "entry", "close (natural)", "close (mid)", "fees", "settled net", "net if closed", "at 2x", "saved"]}
        loading={false}
        rowCount={sessionCloses.length + data.openTags.length}
        empty="no close tagged yet"
        numFrom={5}
        footer="Recorded, never executed: the vertical settled as usual. Net if closed = (entry credit − natural debit) × 100 × qty − the round trip's modelled fees; at 2x adds one more spread (natural − mid). Open rows have no net until they settle."
      >
        {data.openTags.map((t) => (
          <tr key={`open:${t.arm}:${t.positionId}`}>
            <td>{data.session}</td>
            <td>{t.arm}</td>
            <td>{t.source ?? "—"}</td>
            <td>{clock(t.taggedAt)}</td>
            <td>{structure(t.side, t.center, t.wingWidth)}</td>
            <td>{t.spot !== null ? t.spot.toFixed(2) : "—"}</td>
            <td>{fmtPrice(t.credit)}</td>
            <td>{fmtPrice(t.natural !== null ? -t.natural : null)}</td>
            <td>{fmtPrice(t.mid !== null ? -t.mid : null)}</td>
            <td className="muted" colSpan={5}>open: values when it settles</td>
          </tr>
        ))}
        {[...sessionCloses].reverse().map(closeRow)}
      </DataCard>

      <DataCard
        title="spend"
        headers={["session", "target", "checks", "calls", "admissible", "cost", "by model"]}
        loading={false}
        rowCount={spend.length}
        empty="no spend recorded yet"
        numFrom={2}
      >
        {spend.map((s) => (
          <tr key={`${s.session}:${s.target}`}>
            <td>{s.session}</td>
            <td>{s.target}</td>
            <td>{s.checks}</td>
            <td>{s.calls}</td>
            <td>{s.ok}</td>
            <td>{usd(s.costUsd)}</td>
            <td>{Object.entries(s.byModel).map(([m, v]) => `${m} ${String(v.calls)} · ${usd(v.costUsd)}`).join("; ") || "—"}</td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
