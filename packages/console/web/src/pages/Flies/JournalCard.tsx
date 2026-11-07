import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { timeTicks } from "../../components/chart/scales";
import { minuteOf, hhmm } from "../../components/chart/time";
import { HoverReadout } from "../../components/chart/Tooltip";
import { gateDescription } from "./gateGlossary";
import { CHART_GRID, AXIS_MUTED } from "../../components/Charts";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { FliesLayout, FliesSection } from "./FliesFilter";

export interface JournalRow {
  arm: string;
  mode: string;
  reason: string;
  accepted: boolean;
  firstSeen: string | null;
  lastSeen: string | null;
  occurrences: number;
  centerLast: number | null;
  detail: string | null;
}

function wrapLine(text: string, maxChars: number): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxChars && current) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
  }
  if (current) lines.push(current);
  return lines;
}

function useJournal(mode: TradingMode, filter: FliesFilter) {
  return useQuery<{ date: string | null; rows: JournalRow[] }>({
    queryKey: ["flies-journal", mode, filter],
    queryFn: async () => {
      const res = await fetch(`/api/flies/journal?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`journal: HTTP ${res.status}`);
      return (await res.json()) as { date: string | null; rows: JournalRow[] };
    },
    refetchInterval: 30_000,
  });
}

export type Decision = "all" | "taken" | "refused";

const DECISION_WORDS: Record<Decision, string> = { all: "every decision", taken: "entries taken", refused: "refused" };

/** The journal's sorts: timing (when the run began), size (how many ticks it held) and the arm's name. */
export const JOURNAL_SORTS: SortOption<JournalRow>[] = [
  { id: "time", label: "when it began", hint: "earliest first", key: (r) => r.firstSeen },
  { id: "length", label: "how long it held", hint: "longest first", key: (r) => -r.occurrences },
  { id: "arm", label: "arm", hint: "A to Z", key: (r) => r.arm },
];

/** What the journal shows after the arm and decision filters. */
export function journalRows(rows: JournalRow[], arm: string | null, decision: Decision): JournalRow[] {
  return rows.filter((r) => (arm === null || r.arm === arm) && (decision === "all" || (decision === "taken" ? r.accepted : !r.accepted)));
}

/** The ? of the journal: how to read the bars, and what each gate named in the rows means. */
export function journalHelp(rows: JournalRow[]): CardHelpContent {
  const reasons = [...new Set(rows.map((r) => r.reason))].sort();
  const described = reasons.filter((r) => gateDescription(r) !== undefined);
  return {
    title: "Reading the journal",
    body: (
      <>
        <HelpSection title="Bars">
          <p>
            One lane for each arm and mode over the day&apos;s clock. A refusal that held all morning is one bar, as long as it held.
            Green is an entry taken; red is refused, with the gate&apos;s reason.
          </p>
        </HelpSection>
        <HelpSection title="Gates">
          {described.length === 0 ? (
            <p>No gate with a gloss in this session&apos;s rows.</p>
          ) : (
            <dl className="fl-gloss">
              {described.map((r) => (
                <div key={r}>
                  <dt>
                    <code>{r}</code>
                  </dt>
                  <dd>{gateDescription(r)}</dd>
                </div>
              ))}
            </dl>
          )}
        </HelpSection>
      </>
    ),
  };
}

function Gantt({ rows }: { rows: JournalRow[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const spanRows = rows.filter((r) => r.firstSeen !== null && r.lastSeen !== null);
  const lanes = [...new Set(spanRows.map((r) => `${r.arm}|${r.mode}`))].sort();
  if (spanRows.length === 0) return null;

  const width = 1150;
  const pad = { l: 150, r: 12, t: 6, b: 18 };
  const laneH = 22;
  const height = Math.max(56, lanes.length * laneH + pad.t + pad.b);
  const times = spanRows.flatMap((r) => [minuteOf(r.firstSeen!), minuteOf(r.lastSeen!)]);
  const tMin = Math.min(...times);
  let tMax = Math.max(...times);
  if (tMax - tMin < 1) tMax = tMin + 1;
  const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin)) * (width - pad.l - pad.r);

  const bars = spanRows.map((r, i) => {
    const lane = lanes.indexOf(`${r.arm}|${r.mode}`);
    const cy = pad.t + lane * laneH + laneH / 2;
    const x0 = X(minuteOf(r.firstSeen!));
    const bw = Math.max(X(minuteOf(r.lastSeen!)) - x0, 3);
    return { i, r, x0, bw, cy };
  });
  const hovered = hover !== null ? bars[hover] : undefined;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="decision journal"
      style={{ width: "100%", height: "auto", display: "block" }}
      onMouseLeave={() => setHover(null)}
    >
      {timeTicks(tMin, tMax, 8).map((m) => (
        <g key={m}>
          <line x1={X(m)} y1={pad.t} x2={X(m)} y2={height - pad.b} stroke={CHART_GRID} />
          <text x={X(m)} y={height - 5} fontSize={9} fill={AXIS_MUTED} textAnchor="middle" fontFamily="Consolas, monospace">
            {hhmm(m)}
          </text>
        </g>
      ))}
      {lanes.map((lane, li) => {
        const [arm, dmode] = lane.split("|");
        return (
          <text key={lane} x={4} y={pad.t + li * laneH + laneH / 2 + 3} fontSize={10} fill={AXIS_MUTED}>
            {arm} · {dmode}
          </text>
        );
      })}
      {bars.map((b) => (
        <rect
          key={b.i}
          x={b.x0}
          y={b.cy - 6}
          width={b.bw}
          height={12}
          fill={b.r.accepted ? "var(--pos)" : "color-mix(in srgb, var(--neg) 50%, transparent)"}
          onMouseEnter={() => setHover(b.i)}
        />
      ))}
      {hovered !== undefined &&
        (() => {
          const r = hovered.r;
          const description = gateDescription(r.reason);
          const lines = [
            `${r.arm} · ${r.mode}`,
            `${r.accepted ? "✓ " : ""}${r.reason}`,
            `${r.firstSeen!.slice(11, 16)}–${r.lastSeen!.slice(11, 16)} · ${r.occurrences}× seen`,
            ...(description ? wrapLine(description, 60) : []),
          ];
          return <HoverReadout x={hovered.x0} width={width} lines={lines} lineColor={() => "var(--text)"} boxTop={4} />;
        })()}
    </svg>
  );
}

/** The runs, one row each. */
export function JournalRows({ rows }: { rows: JournalRow[] }) {
  return (
    <div className="table-scroll" style={{ marginTop: "0.6rem", maxHeight: "18rem", overflowY: "auto" }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>arm</th>
            <th>mode</th>
            <th>decision</th>
            <th>consecutive rejections</th>
            <th>from</th>
            <th>to</th>
            <th>centre</th>
            <th>detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.arm}</td>
              <td className="muted">{r.mode}</td>
              <td>{r.accepted ? <span className="chain-badge chain-badge-long">{r.reason}</span> : r.reason}</td>
              <td>{r.occurrences}</td>
              <td className="muted">{r.firstSeen?.slice(11, 16) ?? "—"}</td>
              <td className="muted">{r.lastSeen?.slice(11, 16) ?? "—"}</td>
              <td>{r.centerLast !== null ? r.centerLast.toFixed(0) : "–"}</td>
              <td className="muted" style={{ whiteSpace: "normal" }}>
                {r.detail ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The decision journal: a Gantt strip of collapsed runs (one lane per arm·mode; a refusal that held
 * all morning IS an interval, drawn as one bar whose length says how long) over the day's clock, then
 * the runs as rows. Green = entry taken; translucent red = refused. The arm and the decision narrow
 * both; the order of the rows sits behind the same filter & sort.
 */
export function JournalBody({ rows, date, loading }: { rows: JournalRow[]; date: string | null; loading: boolean }) {
  const [sort, setSort] = useSortChoice("fl-decisions", "time");
  const [arm, setArm] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision>("all");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const arms = [...new Set(rows.map((r) => r.arm))].sort();
  const option = JOURNAL_SORTS.find((s) => s.id === sort.id) ?? JOURNAL_SORTS[0];
  const shown = sortRows(journalRows(rows, arm, decision), option, sort.reversed);
  const changed = (arm !== null ? 1 : 0) + (decision !== "all" ? 1 : 0) + (sort.id !== "time" || sort.reversed ? 1 : 0);
  const reset = () => {
    setArm(null);
    setDecision("all");
    setSort({ id: "time", reversed: false });
  };
  const title = `decision journal${date !== null ? ` · ${date}` : ""}`;

  return (
    <FliesLayout>
      <FliesSection concept="risk" name="journal" question="What did each arm decide?">
        <Card
          title={title}
          collapseKey="flies-journal"
          help={journalHelp(rows)}
          controls={rows.length > 0 ? <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} /> : null}
        >
          {loading ? (
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          ) : rows.length === 0 ? (
            <p className="muted">no decisions recorded for this day</p>
          ) : (
            <>
              <FilterSummary
                parts={[arm ?? "all arms", DECISION_WORDS[decision], `by ${option?.label ?? sort.id}${sort.reversed ? ", reversed" : ""}`]}
                note={`${String(shown.length)} of ${String(rows.length)} runs`}
                changed={changed}
                onOpen={() => setPanel(true)}
                onReset={reset}
              />
              {shown.length === 0 ? (
                <p className="muted">no run matches these filters</p>
              ) : (
                <>
                  <Gantt rows={shown} />
                  <JournalRows rows={shown} />
                </>
              )}
              <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
                <FilterGroup title="arm">
                  <div className="ldg-windows" role="group" aria-label="Arm">
                    <button type="button" className={arm === null ? "ldg-win is-on" : "ldg-win"} aria-pressed={arm === null} onClick={() => setArm(null)}>
                      all
                    </button>
                    {arms.map((a) => (
                      <button key={a} type="button" className={arm === a ? "ldg-win is-on" : "ldg-win"} aria-pressed={arm === a} onClick={() => setArm(a)}>
                        {a}
                      </button>
                    ))}
                  </div>
                </FilterGroup>
                <FilterGroup title="decision">
                  <div className="ldg-windows" role="group" aria-label="Decision">
                    {(["all", "taken", "refused"] as const).map((d) => (
                      <button key={d} type="button" className={decision === d ? "ldg-win is-on" : "ldg-win"} aria-pressed={decision === d} onClick={() => setDecision(d)}>
                        {d}
                      </button>
                    ))}
                  </div>
                </FilterGroup>
                <SortSection sorts={JOURNAL_SORTS} choice={sort} fallback="time" onChange={setSort} />
              </FilterSortPanel>
            </>
          )}
        </Card>
      </FliesSection>
    </FliesLayout>
  );
}

export function JournalCard({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const { data, isLoading } = useJournal(mode, filter);
  return <JournalBody rows={data?.rows ?? []} date={data?.date ?? null} loading={isLoading} />;
}
