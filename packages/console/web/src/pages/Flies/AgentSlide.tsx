import { useRef, useState, type ReactNode } from "react";
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
import { Card, PnlCell } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  FilterSwitch,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { useOpenParam } from "../Backtest/CardLightbox";
import { FliesLayout } from "./FliesFilter";
import { Section } from "../../components/Section";
import { BoxOpen, FliesBoxHost, type FliesBox } from "./FliesBoxes";
import { TileGrid } from "../../components/performance/TileGrid";
import { ARM_COLORS, AXIS_FONT_FAMILY, AXIS_MUTED_HEX, SPOT_COLOR } from "../../components/chart/tokens";
import { CHART_GRID } from "../../components/Charts";
import { niceTicks } from "../../components/chart/scales";
import { hhmm, minuteOf } from "../../components/chart/time";
import { fmtCash, fmtPct, fmtPrice } from "../../lib/format";
import { Tile } from "./PerformanceTab";
import { Tile as CountTile } from "../../components/performance/Tile";
import { useTimeline } from "./TimelineCard";

/**
 * The intraday agent (packages/flies/docs/intraday-agent.md): what it decided today, how its
 * paper arm stands against `trend-rule` and `control`, and what the evidence lets live offer.
 *
 * Nothing on this slide judges. Pass/fail, the paired bound, the offered modes and every settled
 * figure are the module's qualification file (`intraday_eval`); "following the agent" is the arm's
 * own rule for a fresh decision. Closes are TAGS: a vertical the rule or the agent would have closed,
 * priced at natural when tagged and left to settle, so "net if closed" is a valuation, never a fill.
 */

const GATE_ON = "var(--amber-line)";
const GATE_OFF = "var(--accent)";
const GATE_BAD = "var(--text-dim)";

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
          <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke={CHART_GRID} />
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
/** A table inside the agent's record lightbox: its caption stays inside, under the rows. */
function RecordTable({
  headers,
  numFrom,
  empty,
  caption,
  rowCount,
  children,
}: {
  headers: string[];
  numFrom?: number;
  empty: string;
  caption?: string;
  rowCount: number;
  children: ReactNode;
}) {
  return (
    <>
      {rowCount === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <div className="table-scroll">
          <table className={`data-table${numFrom !== undefined ? ` num-from-${String(numFrom)}` : ""}`}>
            <thead>
              <tr>{headers.map((h, i) => <th key={`${h}-${String(i)}`}>{h}</th>)}</tr>
            </thead>
            <tbody>{children}</tbody>
          </table>
        </div>
      )}
      {caption ? <p className="muted pf-caption">{caption}</p> : null}
    </>
  );
}

function PackBody({ session, check }: { session: string; check: AgentCheck }) {
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
    <pre className="spec-block" style={{ maxHeight: "none", overflow: "auto" }}>
      {pack.data !== undefined ? JSON.stringify(pack.data.pack, null, 2) : pack.isError ? "pack not readable" : "reading…"}
    </pre>
  );
}

/** The decisions' sorts: timing, cost (size) and the model's confidence. */
export const CHECK_SORTS: SortOption<AgentCheck>[] = [
  { id: "time", label: "time", hint: "earliest first", key: (c) => c.at },
  { id: "cost", label: "cost", hint: "dearest first", key: (c) => (c.costUsd === null ? null : -c.costUsd) },
  { id: "confidence", label: "confidence", hint: "most sure first", key: (c) => (c.confidence === null ? null : -c.confidence) },
];

/** The agent card's ?: what the agent does and what its spend and skipped checks are. */
export const AGENT_HELP: CardHelpContent = {
  title: "The agent",
  body: (
    <>
      <HelpSection title="Paper arm">
        <p>It reads and follows the paper arm only. With no fresh decision for the session the arm runs trend-rule&apos;s gate exactly.</p>
      </HelpSection>
      <HelpSection title="Spend">
        <p>Every model call is costed. A call is made only near the trend band or with a vertical open; skipped checks are recorded too.</p>
      </HelpSection>
      <HelpSection title="The chart">
        <p>
          ■ a model call (amber gate on, blue gate off, grey not admissible) · ✕ an entry the gate refused · ○ ◆ the agent arm&apos;s
          entries and completions · △ a close tagged · dashed: the open and the trend band the gate uses.
        </p>
      </HelpSection>
    </>
  ),
};

export const CHECKS_HELP: CardHelpContent = {
  title: "Decisions",
  body: (
    <>
      <HelpSection title="A row">
        <p>One check: the model, its cost, the gate it chose, the closes it named, its confidence and its reason.</p>
      </HelpSection>
      <HelpSection title="The pack">
        <p>
          Every check is recorded with its pack, called or not: the material a deterministic rule is later fitted from. The pack is what the
          model saw; open a row to read it.
        </p>
      </HelpSection>
    </>
  ),
};

export const RULE_HELP: CardHelpContent = {
  title: "Tagged closes",
  body: (
    <>
      <HelpWarn title="Recorded, never executed">
        <p>The vertical settled as usual; the close is what the agent would have done. Net if closed is a valuation, never a fill.</p>
      </HelpWarn>
      <HelpSection title="Agent − rule">
        <p>The sum of the module&apos;s per-session figures: the agent arm&apos;s settled net less trend-rule&apos;s, over the sessions both have.</p>
      </HelpSection>
    </>
  ),
};

export type RecordTab = "arms" | "offer" | "shadow" | "closes" | "spend";

/** One tab of the agent's record lightbox; each table keeps its caption inside. */
export function RecordBody({ data, tab }: { data: FliesAgentPayload; tab: RecordTab }) {
  const q = data.qualification;
  const sessionCloses = q?.taggedCloses ?? [];
  const perSession = [...(q?.perSession ?? [])].reverse();
  const spend = [...(q?.spend ?? [])].reverse();
  switch (tab) {
    case "offer":
      return (
        <RecordTable
          headers={["unlocks", "criterion", "value", "needs", ""]}
          rowCount={q?.criteria.length ?? 0}
          empty={data.qualificationStatus === "failed" ? "the qualification file could not be read" : "not evaluated yet"}
          caption={
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
        </RecordTable>
      );
    case "shadow":
      return (
        <RecordTable
          headers={["session", "entries", "gate would refuse", "live effect", "paper effect", "counted", "agree"]}
          numFrom={1}
          rowCount={q?.shadow.sessions.length ?? 0}
          empty="no live session run with the agent in shadow yet"
          caption={
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
        </RecordTable>
      );
    case "closes":
      return (
        <RecordTable
          headers={["session", "arm", "by", "tagged", "vertical", "spot", "entry", "close (natural)", "close (mid)", "fees", "settled net", "net if closed", "at 2x", "saved"]}
          numFrom={5}
          rowCount={sessionCloses.length + data.openTags.length}
          empty="no close tagged yet"
          caption="Recorded, never executed: the vertical settled as usual. Net if closed = (entry credit − natural debit) × 100 × qty − the round trip's modelled fees; at 2x adds one more spread (natural − mid). Open rows have no net until they settle."
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
        </RecordTable>
      );
    case "spend":
      return (
        <RecordTable headers={["session", "target", "checks", "calls", "admissible", "cost", "by model"]} numFrom={2} rowCount={spend.length} empty="no spend recorded yet">
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
        </RecordTable>
      );
    default:
      return (
        <RecordTable
          headers={["session", "source", "decided", "control", "trend-rule", "agent", "agent − rule", "rule strands", "agent strands", "agent if closed (2x)"]}
          numFrom={3}
          rowCount={perSession.length}
          empty={data.qualificationStatus === "absent" ? "not evaluated yet: `run.py agent-eval --write`, or the next settlement" : "no settled session for the rule or agent arm yet"}
          caption="Settled net, after fees. Strands are uncompleted verticals over entries. The last column values the agent arm's tagged closes at natural plus one more spread's worth. Replay rows are control's recorded entries less those each gate refuses; past sessions kept no quotes, so they value no close."
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
        </RecordTable>
      );
  }
}

export function AgentSlide({ mode, date }: { mode: TradingMode; date: string | null }) {
  const { data, isLoading, isError, dataUpdatedAt } = useAgent(date);
  const [showSkips, setShowSkips] = useState(false);
  const [sort, setSort] = useSortChoice("fl-agent-checks", "time");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const packs = useOpenParam("pack");
  const record = useOpenParam("rec");

  // Paper-only until the live shadow is scored (step 4): refuse a live read rather than show the
  // paper arm under a live badge.
  if (mode === "live") {
    return (
      <FliesLayout>
        <Section concept="stand" tag="the agent" question="What is the agent doing?">
          <Card title="intraday agent">
            <p className="muted">
              This tab reads the paper arm and the module&apos;s qualification file, so it has no live view. The live shadow (chosen
              per day at /live-flies-start) is scored there and shown under &quot;live shadow&quot; in the Paper view.
            </p>
          </Card>
        </Section>
      </FliesLayout>
    );
  }

  if (data === undefined) {
    return (
      <FliesLayout>
        <Section concept="stand" tag="the agent" question="What is the agent doing?">
          <Card title="intraday agent">
            <p className="muted">{isLoading ? "reading…" : isError ? "the agent payload could not be read" : "—"}</p>
          </Card>
        </Section>
      </FliesLayout>
    );
  }

  const q = data.qualification;
  const d = data.decision;
  const cfg = data.config;
  const paperChecks = data.checks.filter((c) => c.target === "paper");
  const calls = paperChecks.filter((c) => c.called);
  const decisionValue = d?.fresh === true ? `gate ${d.trendGate ?? "?"}` : "rule";
  const option = CHECK_SORTS.find((s) => s.id === sort.id) ?? CHECK_SORTS[0];
  const rows = sortRows(showSkips ? paperChecks : calls, option, sort.reversed);
  const sessionCloses = q?.taggedCloses ?? [];
  const spend = [...(q?.spend ?? [])].reverse();
  const decided = q?.sessions.decided ?? null;
  const minSessions = q?.criteria.find((c) => c.id === "decision_sessions")?.threshold ?? null;
  const gaps = (q?.perSession ?? []).map((r) => r.agentLessRule).filter((v): v is number => v !== null);
  const gapSum = gaps.length > 0 ? gaps.reduce((a, b) => a + b, 0) : null;
  const changed = (showSkips ? 1 : 0) + (sort.id !== "time" || sort.reversed ? 1 : 0);
  const reset = () => {
    setShowSkips(false);
    setSort({ id: "time", reversed: false });
  };

  const session = data.session;
  const packBoxes: FliesBox[] =
    session === null
      ? []
      : rows
          .filter((c) => c.called)
          .map((c) => ({
            id: String(c.index),
            kicker: "decisions ›",
            title: `Pack · ${clock(c.at)}`,
            chips: c.reason !== null ? <span className="chip">{c.reason}</span> : undefined,
            tabs: [{ id: "pack", label: "what the model saw" }],
            body: () => <PackBody session={session} check={c} />,
          }));

  const recordBox: FliesBox = {
    id: "record",
    kicker: "against the rule ›",
    title: "The agent's record",
    tabs: [
      { id: "arms", label: "arms by session" },
      { id: "offer", label: "live may offer" },
      { id: "shadow", label: "live shadow" },
      { id: "closes", label: "tagged closes" },
      { id: "spend", label: "spend" },
    ] satisfies Array<{ id: RecordTab; label: string }>,
    body: (tab) => <RecordBody data={data} tab={tab as RecordTab} />,
  };

  return (
    <FliesLayout>
      <Section concept="stand" tag="the agent" question="What is the agent doing?">
        <Card title="intraday agent" collapseKey="flies-agent" updatedAt={dataUpdatedAt} help={AGENT_HELP} className="fl-wide">
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
              title={`capped by live_mode_max (${q?.liveModeMax ?? cfg?.liveModeMax ?? "?"}) and by the criteria in the record`}
            />
          </TileGrid>
          {cfg !== null && !cfg.enabled && <p className="muted">The agent is off in flies.json (intraday_agent.enabled); the arm runs the fixed rule.</p>}
          <div className="fl-chart">
            <div className="card-subhead">{`the session${data.session !== null ? ` · ${data.session}` : ""}`}</div>
            {data.session !== null ? (
              <DecisionChart data={data} />
            ) : (
              <p className="muted">No agent session recorded yet: the job&apos;s first check runs at 09:30 on a session day.</p>
            )}
          </div>
        </Card>
      </Section>

      <Section concept="hist" tag="record" question="What has it decided?">
        <Card
          title="decisions"
          collapseKey="flies-agent-decisions"
          className="fl-8"
          help={CHECKS_HELP}
          controls={<FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />}
        >
          <FilterSummary
            parts={[showSkips ? "every check" : "model calls only", `by ${option?.label ?? sort.id}${sort.reversed ? ", reversed" : ""}`]}
            note={`${String(rows.length)} of ${String(paperChecks.length)} checks`}
            changed={changed}
            onOpen={() => setPanel(true)}
            onReset={reset}
          />
          {rows.length === 0 ? (
            <p className="muted">{paperChecks.length === 0 ? "no checks recorded for this session" : "no model calls this session"}</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    {["time", "check", "model", "cost", "secs", "gate", "closes", "conf.", "reason", ""].map((h, i) => (
                      <th key={`${h}-${String(i)}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.index}>
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
                        {c.called && session !== null && (
                          <button type="button" className="pf-more" onClick={() => packs.show(String(c.index))} aria-label={`Open the pack for ${clock(c.at)}`}>
                            pack <span aria-hidden="true">›</span>
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
            <FilterGroup title="show">
              <FilterSwitch label="skipped checks" description="every check is recorded, called or not" on={showSkips} onChange={setShowSkips} />
            </FilterGroup>
            <SortSection sorts={CHECK_SORTS} choice={sort} fallback="time" onChange={setSort} />
          </FilterSortPanel>
        </Card>

        <Card title="against the rule" collapseKey="flies-agent-rule" className="fl-4" help={RULE_HELP} flag="recorded, never executed">
          <TileGrid count={3}>
            <CountTile label="agent − rule" value={fmtCash(gapSum)} tone={gapSum === null ? "dim" : gapSum >= 0 ? "pos" : "neg"} n={gaps.length} nUnit="sessions" afterFees />
            <Tile label="tagged closes" value={String(sessionCloses.length + data.openTags.length)} />
            <Tile label="spend, all sessions" value={usd(spend.length > 0 ? spend.reduce((a, s) => a + (s.costUsd ?? 0), 0) : null)} tone="dim" />
          </TileGrid>
          <div className="pf-mores pf-push">
            <BoxOpen lb={record} id="record" hot>the agent&apos;s record</BoxOpen>
          </div>
        </Card>
      </Section>
      <FliesBoxHost lb={packs} boxes={packBoxes} />
      <FliesBoxHost lb={record} boxes={[recordBox]} />
    </FliesLayout>
  );
}
