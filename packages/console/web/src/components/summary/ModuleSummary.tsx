import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { ModuleSummarySlice, SummaryBreakdown, SummaryTile, SummaryUnit, TradingMode } from "@console/shared";
import { TESTED_STATUS } from "../../lib/testedStatus";
import { useOpenParam } from "../../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost } from "../../pages/Flies/FliesBoxes";
import { ChoiceGroup } from "../../pages/Flies/FliesFilter";
import { HelpSection, type CardHelpContent } from "../CardHelp";
import { Card } from "../DataTable";
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
} from "../FilterSort";
import { Section } from "../Section";
import { etToday } from "../table/DateRange";
import { Gauge, NetHero, RangeToolbar, TsCard, TsEmpty, WinRateArcs, fmtUsd, fmtUsdPlain, sessionLabel, useFace, useSummaryRange } from "./kit";

/**
 * A module's simplified-view summary: the MEIC summary's pattern over `/api/summary/:module`.
 * The component is shared; WHAT it shows is the module reader's choice (readers/summary/), in the
 * module's own vocabulary. It derives nothing but presentation. Loading, error and empty are
 * three different statements, never a wall of zeros.
 *
 * Laid out in the suite's sections (2026-11-07): the window's numbers, then where they came from
 * (the breakdowns and the arms, each with its ordering behind filter & sort and its long tables in a
 * lightbox), then the notes. What explains a card sits behind its ?.
 */

/** The page header's scope, as the manifest holds it: the arm / symbol / era dropdowns. */
export interface SummaryScope {
  arm?: string | null;
  symbol?: string | null;
  era?: string | null;
}

export function useModuleSummary(module: string, mode: TradingMode, range: string, day: string | null, scope?: SummaryScope) {
  const params = new URLSearchParams({ mode, range });
  if (day !== null) params.set("date", day);
  if (scope?.arm != null && scope.arm !== "") params.set("arm", scope.arm);
  if (scope?.symbol != null && scope.symbol !== "") params.set("symbol", scope.symbol);
  if (scope?.era != null && scope.era !== "") params.set("era", scope.era);
  return useQuery<ModuleSummarySlice>({
    queryKey: ["module-summary", module, mode, range, day, scope?.arm ?? null, scope?.symbol ?? null, scope?.era ?? null],
    queryFn: async () => {
      const res = await fetch(`/api/summary/${module}?${params.toString()}`);
      if (!res.ok) throw new Error(`summary: HTTP ${res.status}`);
      return (await res.json()) as ModuleSummarySlice;
    },
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function fmtValue(value: number | null, unit: SummaryUnit): string {
  if (value === null) return "n/r";
  switch (unit) {
    case "usd":
      return fmtUsd(value);
    case "pct":
      return `${value.toFixed(1)}%`;
    case "ratio":
      return value.toFixed(2);
    case "price":
      return value.toFixed(2);
    case "days":
      return `${value.toFixed(1)} d`;
    default:
      return value.toLocaleString("en-US");
  }
}

/** The width a row of `n` cards gives each (fl-w3 four to a row, fl-w4 three, fl-w6 two). */
export function widthOf(n: number): string {
  return n >= 4 ? "fl-w3" : n === 3 ? "fl-w4" : "fl-w6";
}

function netHelp(unit: string | null): CardHelpContent {
  return {
    title: "Net P&L",
    body: (
      <>
        <HelpSection title="What is counted">
          <p>
            Gross is the trades&apos; result before costs; each cost sits on its own line beneath it, and net is what is left. A cost the module
            did not record reads n/r, never zero.
          </p>
        </HelpSection>
        {unit && (
          <HelpSection title="What one trade is here">
            <p>One trade here is {unit}.</p>
          </HelpSection>
        )}
        <HelpSection title="The figure">
          <p>Click the big figure to swap the window&apos;s total for net per trade, with its median, average win and loss, worst trade and interval.</p>
        </HelpSection>
      </>
    ),
  };
}

function NetCard({ s, label, className }: { s: ModuleSummarySlice; label: string; className: string }) {
  const { hero } = s;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{label}</span>} help={netHelp(s.unit ?? null)} className={className}>
      <NetHero net={hero.net} trades={hero.trades} shape={hero.shape} />
      <dl className="ts-kv">
        <div>
          <dt>gross</dt>
          <dd className={hero.gross >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(hero.gross)}</dd>
        </div>
        {hero.costs.map((c) => (
          <div key={c.label}>
            <dt>{c.label}</dt>
            {c.amount === null ? (
              <dd className="ts-muted" title={c.note}>
                n/r
              </dd>
            ) : (
              <dd className="ts-neg">−{fmtUsdPlain(c.amount)}</dd>
            )}
          </div>
        ))}
        {hero.basis && (
          <div>
            <dt>{hero.basis.label}</dt>
            <dd>{fmtUsdPlain(hero.basis.amount)}</dd>
          </div>
        )}
        <div>
          <dt>open now</dt>
          <dd>{hero.open}</dd>
        </div>
      </dl>
    </TsCard>
  );
}

function Tiles({ tiles }: { tiles: SummaryTile[] }) {
  if (tiles.length === 0) return null;
  return (
    <Card title="the window in figures" collapseKey="ts-tiles" className="lv-12">
      <div className="ts-tiles">
        {tiles.map((t) => {
          const tone = t.signed === false || t.value === null ? "" : t.value >= 0 ? "ts-pos" : "ts-neg";
          return (
            <div key={t.label} className="ts-tile">
              <div className="ts-muted ts-tile-label">{t.label}</div>
              <div className={`ts-tile-value ${tone}`}>{fmtValue(t.value, t.unit)}</div>
              {t.note && <div className="ts-muted ts-tile-note">{t.note}</div>}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/** A breakdown's two faces: the bar per trade (the default -- totals favour whichever bucket traded
 *  most) or total (what each bucket contributed). Both columns stay in the table; the bar and the
 *  emphasis follow the face. */
const BREAKDOWN_FACES = ["per trade", "total"] as const;

/** One row of a breakdown, an arm's row too: the arm table is a breakdown whose bucket is the arm. */
interface BucketRow {
  name: string;
  trades: number;
  winPct: number | null;
  avgNet: number | null;
  net: number;
  retired?: boolean;
}

/** How many rows a card shows before the rest wait in its lightbox. */
export const PREVIEW_ROWS = 6;

/** The orderings: the module's own order, then size and name, then net (the old column sort). The
 *  net key follows the face, as the emphasis does. Nothing here ranks on credit or yield. */
export function bucketSorts(face: (typeof BREAKDOWN_FACES)[number]): SortOption<BucketRow>[] {
  return [
    { id: "read", label: "as the module orders them", hint: "module order", key: () => 0 },
    { id: "size", label: "trades", hint: "most trades first", key: (r) => -r.trades },
    { id: "name", label: "name", hint: "A to Z", key: (r) => r.name },
    { id: "net", label: face === "total" ? "net" : "net per trade", hint: "best first", key: (r) => (face === "total" ? -r.net : r.avgNet === null ? null : -r.avgNet) },
  ];
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function BucketTable({ rows, column, face }: { rows: BucketRow[]; column: string; face: (typeof BREAKDOWN_FACES)[number] }) {
  const barOf = (r: BucketRow) => (face === "total" ? r.net : (r.avgNet ?? 0));
  const max = Math.max(...rows.map((r) => Math.abs(barOf(r))), 1);
  return (
    <div className="table-scroll">
      <table className="ts-table">
        <thead>
          <tr>
            <th>{column}</th>
            <th className="num">n</th>
            <th className="num">win</th>
            <th className="num">avg</th>
            <th className="num">net</th>
            <th aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className={r.retired ? "ts-muted" : undefined}>
              <td>
                {r.name}
                {r.retired ? " (retired)" : ""}
              </td>
              <td className="num">{r.trades}</td>
              <td className="num">{r.winPct === null ? "n/r" : `${r.winPct.toFixed(0)}%`}</td>
              <td className={`num ${r.avgNet === null ? "" : r.avgNet >= 0 ? "ts-pos" : "ts-neg"}${face === "per trade" ? " ts-strong" : ""}`}>{r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}</td>
              <td className={`num ${r.net >= 0 ? "ts-pos" : "ts-neg"}${face === "total" ? " ts-strong" : ""}`}>{fmtUsd(r.net)}</td>
              <td style={{ width: "22%" }}>
                <div className="ts-hbar">
                  <div className={`ts-hbar-fill ${barOf(r) >= 0 ? "ts-pos" : "ts-neg"}`} style={{ width: `${((Math.abs(barOf(r)) / max) * 100).toFixed(1)}%` }} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One breakdown, or the arms: a short table with the bar per trade or total, its face and its order
 * behind filter & sort, and the rest of a long table in a lightbox (`?sum-<title>=all`). Retired
 * arms can be hidden; the summary line says what is on.
 */
export function BreakdownCard({ title, column, rows, note, className }: { title: string; column: string; rows: BucketRow[]; note?: string | null; className: string }) {
  const [face, , setFace] = useFace(`breakdown:${title}`, BREAKDOWN_FACES);
  const [sort, setSort] = useSortChoice(`ms-${slug(title)}`, "read");
  const [hideRetired, setHideRetired] = useState(false);
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const lb = useOpenParam(`sum-${slug(title)}`);
  if (rows.length === 0) return <TsEmpty what={title} className={className} />;

  const sorts = bucketSorts(face);
  const option = sorts.find((x) => x.id === sort.id) ?? sorts[0];
  const hasRetired = rows.some((r) => r.retired);
  const kept = hideRetired ? rows.filter((r) => !r.retired) : rows;
  const ordered = sortRows(kept, option, sort.reversed);
  const shown = ordered.slice(0, PREVIEW_ROWS);
  const changed = (face !== "per trade" ? 1 : 0) + (sort.id !== "read" || sort.reversed ? 1 : 0) + (hideRetired ? 1 : 0);
  const reset = () => {
    setFace("per trade");
    setSort({ id: "read", reversed: false });
    setHideRetired(false);
  };
  const box = {
    id: "all",
    kicker: `${title} ›`,
    title: `${title}: every ${column}`,
    tabs: [{ id: "all", label: `${ordered.length} ${column === "arm" ? "arms" : "rows"}` }],
    body: () => <BucketTable rows={ordered} column={column} face={face} />,
  };
  return (
    <Card
      title={title}
      collapseKey={`ts-${title}`}
      className={className}
      help={{
        title,
        body: (
          <>
            <HelpSection title="What a row is">
              <p>
                Each {column} with its settled trades (n), the share that won, the average per trade and the net. The bar follows the figure you
                choose under filter &amp; sort: per trade by default, since totals favour whichever {column} traded most.
              </p>
            </HelpSection>
            {note && (
              <HelpSection title="Scope">
                <p>{note}</p>
              </HelpSection>
            )}
          </>
        ),
      }}
      controls={<FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />}
    >
      <FilterSummary
        parts={[face, `by ${option?.label ?? sort.id}${sort.reversed ? ", reversed" : ""}`, ...(hideRetired ? ["retired hidden"] : [])]}
        note={`${String(shown.length)} of ${String(ordered.length)} shown`}
        changed={changed}
        onOpen={() => setPanel(true)}
        onReset={reset}
      />
      <BucketTable rows={shown} column={column} face={face} />
      {ordered.length > shown.length && (
        <div className="pf-mores pf-push">
          <BoxOpen lb={lb} id="all" hot>
            all {ordered.length} {column === "arm" ? "arms" : "rows"}
          </BoxOpen>
        </div>
      )}
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <FilterGroup title="figure">
          <ChoiceGroup label="Figure" value={face} options={BREAKDOWN_FACES.map((f) => ({ id: f, label: f }))} onChange={setFace} />
        </FilterGroup>
        {hasRetired && (
          <FilterGroup title="show">
            <FilterSwitch label="hide retired arms" description="leave out arms that no longer run" on={hideRetired} onChange={setHideRetired} />
          </FilterGroup>
        )}
        <SortSection sorts={sorts} choice={sort} fallback="read" onChange={setSort} />
      </FilterSortPanel>
      <FliesBoxHost lb={lb} boxes={[box]} />
    </Card>
  );
}

const asRows = (b: SummaryBreakdown): BucketRow[] => b.rows.map((r) => ({ name: r.bucket, trades: r.trades, winPct: r.winPct, avgNet: r.avgNet, net: r.net }));

const armRows = (s: ModuleSummarySlice): BucketRow[] =>
  s.arms.map((a) => ({ name: a.arm, trades: a.trades, winPct: a.winPct, avgNet: a.trades > 0 ? a.net / a.trades : null, net: a.net, retired: a.retired }));

/**
 * The line above the numbers: which ledger this is, whether live has traded at all, and what history
 * says about the strategy. A day window older than today says "last traded" so it never reads as today.
 */
export function StatusLine({
  module,
  mode,
  neverLive = false,
  lastTraded = null,
}: {
  module: string;
  mode: TradingMode;
  /** Live, and the live ledger has no row at all. */
  neverLive?: boolean;
  /** The latest window is a day before today: that day. */
  lastTraded?: string | null;
}) {
  const tested = TESTED_STATUS[module];
  return (
    <p className="ts-muted ts-status" role="note">
      <strong>{mode === "live" ? "Live" : "Paper"}.</strong>
      {neverLive && <> Live has not traded yet.</>}
      {lastTraded !== null && <> Nothing traded today; this is the last traded day, {sessionLabel(lastTraded)}.</>}
      {tested && (
        <>
          {" "}
          {tested.words} <span title={tested.source}>({tested.source})</span>
        </>
      )}
    </p>
  );
}

export function ModuleSummary({
  module,
  mode,
  scope,
  day,
  onDay,
  today,
}: {
  module: string;
  mode: TradingMode;
  /** The module's own session cards (open now, today's entries and why), above the window's numbers.
   *  Summary and session were two tabs until 2026-10-30 (docs/console-review-plan.md, batch 2). */
  today?: ReactNode;
  /** The page header's arm/symbol/era, so the summary narrows the way the page beside it does. */
  scope?: SummaryScope;
  /** The page's session. `undefined` = drive it from this component's own `?day=` (a module with
   *  no session control in its header); a value (null = latest) = the header owns the day. */
  day?: string | null;
  /** Step the page's session to `day` (absolute, the reader's own neighbour). When given, the
   *  arrows move the header's day; when absent, they write this component's own `?day=`. */
  onDay?: (day: string | null) => void;
}) {
  const [range, setRange] = useSummaryRange();
  const [params, setParams] = useSearchParams();
  const stated = params.get("day");
  const urlDay = stated !== null && /^\d{4}-\d{2}-\d{2}$/.test(stated) ? stated : null;
  const effectiveDay = day !== undefined ? day : urlDay;
  const q = useModuleSummary(module, mode, range, effectiveDay, scope);
  if (q.isError) {
    return (
      <div className="ts-root">
        {today !== undefined && <div className="ts-today">{today}</div>}
        <p className="ts-empty">the summary read failed — {q.error instanceof Error ? q.error.message : "unknown error"}</p>
      </div>
    );
  }
  const s = q.data ?? null;
  const step = (delta: -1 | 1) => {
    const target = delta < 0 ? s?.session.prev : s?.session.next;
    if (!target) return;
    if (onDay !== undefined) {
      onDay(target);
      return;
    }
    const next = new URLSearchParams(params);
    next.set("day", target);
    setParams(next, { replace: true });
  };
  const etDay = etToday();
  const stale = s !== null && effectiveDay === null && s.window.label === "day" && s.window.from !== null && s.window.from < etDay;
  const label =
    s === null
      ? "…"
      : s.window.label === "day" && s.window.from !== null
        ? `${stale ? "last traded " : ""}${sessionLabel(s.window.from)}`
        : s.window.label;
  const arm = scope?.arm;
  const armNote = arm != null && arm !== "" ? `The ${arm} arm only.` : "Every arm in the window.";
  return (
    <div className="ts-root">
      {/* The session first, on its own read, so it never waits on the window's numbers below. */}
      {today !== undefined && <div className="ts-today">{today}</div>}
      <RangeToolbar
        range={range}
        onRange={setRange}
        label={label}
        count={`${s?.hero.trades ?? 0} settled`}
        prevDisabled={!s?.session.prev}
        nextDisabled={!s?.session.next}
        onDayStep={step}
      />
      {q.isLoading && <p className="ts-empty">reading the ledger…</p>}
      {s !== null && (
        <>
          <StatusLine
            module={module}
            mode={s.mode}
            neverLive={s.mode === "live" && s.window.from === null && s.hero.trades === 0 && s.hero.open === 0}
            lastTraded={stale ? s.window.from : null}
          />
          {s.hero.trades === 0 && s.hero.open === 0 ? (
            <p className="ts-empty">no settled trades in this window</p>
          ) : (
            <div className="pf-layout">
              <Section concept="stand" tag="the window" question="How did this window go?">
                {(() => {
                  const w = widthOf(s.gauges.length + 2);
                  return (
                    <>
                      <NetCard s={s} label={label} className={w} />
                      {s.gauges.map((g) => (
                        <Gauge key={g.title} title={g.title} rawPct={g.rawPct} min={g.min} max={g.max} caption={g.caption} dollars={g.dollars} className={w} />
                      ))}
                      <WinRateArcs
                        daysWon={s.hero.daysWon}
                        days={s.hero.daysWon + s.hero.daysLost}
                        wins={s.hero.wins}
                        trades={s.hero.trades}
                        avgWin={s.hero.shape?.avgWin ?? null}
                        avgLoss={s.hero.shape?.avgLoss ?? null}
                        shape={s.hero.shape ?? null}
                        className={w}
                      />
                    </>
                  );
                })()}
                <Tiles tiles={s.tiles} />
              </Section>
              {s.breakdowns.length > 0 && (
                <Section concept="edge" tag="breakdowns" question="Where did the result come from?">
                  {s.breakdowns.map((b) => (
                    <BreakdownCard key={b.title} title={b.title} column={b.column} rows={asRows(b)} className={s.breakdowns.length >= 3 ? "fl-w4" : "fl-w6"} />
                  ))}
                </Section>
              )}
              <Section concept="dir" tag="by arm" question="Which arm made it?">
                <BreakdownCard title="by arm" column="arm" rows={armRows(s)} note={armNote} className="lv-12" />
              </Section>
            </div>
          )}
          {s.notes.length > 0 && (
            <div className="pf-layout">
              <Section concept="risk" tag="caveats" question="What should I keep in mind about these numbers?">
                <Card title="notes on this window" collapseKey="ts-notes" flag={`${String(s.notes.length)} note${s.notes.length === 1 ? "" : "s"}`} help={NOTES_HELP}>
                  <ul className="ts-notes ts-muted">
                    {s.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </Card>
              </Section>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const NOTES_HELP: CardHelpContent = {
  title: "Notes on this window",
  body: (
    <HelpSection title="Where they come from">
      <p>The module&apos;s own statements about how this window was measured: what is missing, what changed part-way, what a figure leaves out.</p>
    </HelpSection>
  ),
};
