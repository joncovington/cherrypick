import { Fragment, useRef, useState, type ReactNode } from "react";
import type {
  RegimeArm,
  RegimeCell,
  RegimeCrossCell,
  RegimeCrossTab,
  RegimeCuts,
  RegimeCutsModule,
  RegimeMultiplicity,
  RegimePair,
} from "@console/shared";
import { useRegimeCuts } from "../lib/api";
import { Card, PnlCell, SkeletonRows, SortTh, fmtMoney, sortRows, useSort } from "./DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "./CardHelp";
import { FilterGroup, FilterSortButton, FilterSortPanel, FilterSummary } from "./FilterSort";
import { Section } from "./Section";
import { useOpenParam } from "../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox, type OpenParam } from "../pages/Flies/FliesBoxes";

/**
 * The regime-cuts slide (2026-09-19): the module's own nightly artifact, one table per regime
 * dimension (rows = arms, columns = the buckets that occur) and one grid per declared cross-tab
 * (one small multiple per arm), rendered as written. This component derives nothing: `thin` is
 * the writer's flag, the era and every number come off the file, and the only layout decision
 * made here is which rows and columns to draw, from the buckets present. Absent, failed and
 * stale are shown as three different things. The 2026-09-28 stamps -- `fragile`, `robustness`,
 * `history`, `paired`, `multiplicity` -- are the writer's too and are printed, never recomputed;
 * an artifact written before them simply shows no markers.
 */

const WRITE_COMMAND: Record<RegimeCutsModule, string> = {
  flies: "python run.py regime-cuts --write",
  meic: "python -m cherrypick.meic.regime_cuts --write",
};

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(0)}%`;
}

/** completion% when the module has the concept, else win%; then net; then sessions. */
function cellText(c: RegimeCell | RegimeCrossCell): string {
  const rate = c.completionRate !== null ? pct(c.completionRate) : pct(c.winRate);
  return `${rate} · ${c.netPnl === null ? "—" : fmtMoney(c.netPnl)} · ${c.sessions}s`;
}

function cellTitle(c: RegimeCell | RegimeCrossCell): string {
  const parts = [`${c.trades} trades`, `${c.sessions} sessions`];
  if (c.completed !== null) parts.push(`${c.completed} completed`);
  if (c.winRate !== null) parts.push(`win ${pct(c.winRate)}`);
  if (c.avgPnl !== null) parts.push(`avg ${fmtMoney(c.avgPnl)}`);
  if (c.thin) parts.push("thin: fewer sessions than the writer's floor");
  return parts.join(" · ");
}

function ThinMark() {
  return (
    <span
      className="muted"
      style={{ fontSize: 10, marginLeft: 4 }}
      title="Fewer sessions than the writer's floor — same-day entries share a regime, so this is one or two independent observations however many trades it holds. Flagged by the module, not here."
    >
      thin
    </span>
  );
}

/** The writer's `fragile` stamp, with the numbers behind it. Same family as ThinMark. */
function FragileMark({ c }: { c: RegimeCell | RegimeCrossCell }) {
  const r = c.robustness;
  const parts = ["Fragile, stamped by the module"];
  if (r !== null) {
    if (r.largestSessionShare !== null)
      parts.push(`largest session ${fmtMoney(r.largestSessionNet)} is ${pct(r.largestSessionShare)} of the cell's absolute flow`);
    if (r.signFlipsDroppingOne !== null)
      parts.push(`dropping one session flips the sign in ${r.signFlipsDroppingOne} case${r.signFlipsDroppingOne === 1 ? "" : "s"}`);
    if (r.netInterval !== null)
      parts.push(
        `${r.intervalLevel === null ? "" : `${pct(r.intervalLevel)} `}interval ${fmtMoney(r.netInterval[0])} to ${fmtMoney(r.netInterval[1])}`,
      );
  }
  return (
    <span style={{ fontSize: 10, marginLeft: 4, color: "var(--warn)" }} title={parts.join(" · ")}>
      fragile
    </span>
  );
}

/** The writer's history stamp, shown only when the cell's net has changed sign over its snapshots. */
function SignChangeMark({ c }: { c: RegimeCell | RegimeCrossCell }) {
  const h = c.history;
  if (h === null || h.signChanges <= 0) return null;
  return (
    <span
      style={{ fontSize: 10, marginLeft: 4, color: "var(--warn)" }}
      title={`Net has changed sign ${h.signChanges}× — first ${fmtMoney(h.firstNet)} → now ${fmtMoney(c.netPnl)}, over ${h.snapshots} prior snapshot${h.snapshots === 1 ? "" : "s"}. Stamped by the module.`}
    >
      ±{h.signChanges}
    </span>
  );
}

function StampMarks({ c }: { c: RegimeCell | RegimeCrossCell }) {
  return (
    <>
      {c.fragile === true && <FragileMark c={c} />}
      <SignChangeMark c={c} />
    </>
  );
}

const lastBucket = (k: string): number => (k === "untagged" || k === "unknown" ? 1 : 0);

function byTradesDesc(totals: Map<string, number>): string[] {
  return [...totals.entries()]
    .sort((a, b) => lastBucket(a[0]) - lastBucket(b[0]) || b[1] - a[1] || a[0].localeCompare(b[0]))
    .map((e) => e[0]);
}

function bucketColumns(arms: RegimeArm[], dim: string): string[] {
  // Union of buckets across arms, ordered by total trades desc, untagged/unknown last -- the one
  // layout decision this slide makes. The writer already orders each arm's own buckets that way.
  const totals = new Map<string, number>();
  for (const b of arms) for (const c of b.dimensions[dim]?.buckets ?? []) totals.set(c.bucket, (totals.get(c.bucket) ?? 0) + c.trades);
  return byTradesDesc(totals);
}

/** One axis of a cross-tab: the buckets that occur at position `i` of every cell's pair. */
function crossAxis(tab: RegimeCrossTab, i: number): string[] {
  const totals = new Map<string, number>();
  for (const b of tab.arms) for (const c of b.cells) {
    const k = c.buckets[i];
    if (k !== undefined) totals.set(k, (totals.get(k) ?? 0) + c.trades);
  }
  return byTradesDesc(totals);
}

const cellRate = (c: RegimeCrossCell): number | null => c.completionRate ?? c.winRate;

export function CrossTabGrid({ tab }: { tab: RegimeCrossTab }) {
  const rows = crossAxis(tab, 0);
  const cols = crossAxis(tab, 1);
  return (
    <>
      <div className="regime-grid-arms">
        {tab.arms.map((b) => {
          const byKey = new Map(b.cells.map((c) => [c.buckets.join("\u0000"), c] as const));
          return (
            <div className="regime-grid-arm" key={b.arm}>
              <h3>{b.arm}</h3>
              <div
                className="regime-grid"
                style={{ gridTemplateColumns: `auto repeat(${cols.length}, minmax(3.2rem, 1fr))` }}
              >
                <div />
                {cols.map((col) => (
                  <div className="regime-grid-head" key={col}>
                    {col}
                  </div>
                ))}
                {rows.map((row) => (
                  <Fragment key={row}>
                    <div className="regime-grid-head">{row}</div>
                    {cols.map((col) => {
                      const c = byKey.get(`${row}\u0000${col}`);
                      if (c === undefined || c.trades === 0)
                        return (
                          <div className="regime-grid-cell muted" key={col}>
                            —
                          </div>
                        );
                      const rate = cellRate(c);
                      // The thin flag outranks the rate: a two-session cell reading 90% is the
                      // misread the writer's flag exists to prevent, so it never gets a colour.
                      const colour =
                        c.thin || rate === null
                          ? "var(--track)"
                          : `color-mix(in srgb, var(--pos) ${Math.round((0.15 + 0.85 * rate) * 100)}%, transparent)`;
                      return (
                        <div
                          className={`regime-grid-cell${c.thin || rate === null ? " muted" : ""}`}
                          key={col}
                          style={{ background: colour }}
                          title={cellTitle(c)}
                        >
                          <span>{c.sessions}s</span>
                          {c.thin ? <ThinMark /> : rate !== null && <span>{pct(rate)}</span>}
                          <StampMarks c={c} />
                        </div>
                      );
                    })}
                  </Fragment>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

/** The cross-tab card's ?: how to read the grid, with the read-sessions-first warning leading. */
export function crossTabHelp(tab: RegimeCrossTab, thinBelowSessions: number | null): CardHelpContent {
  const rateLabel = tab.arms.some((b) => b.cells.some((c) => c.completionRate !== null)) ? "completion" : "win";
  return {
    title: `By ${tab.dims.join(" × ")}`,
    body: (
      <>
        <HelpWarn title="Read sessions first">
          <p>
            A declared cross-tab. The first exists because a single-dimension cut once made net-GEX sign look predictive until the trend
            bucket showed the whole effect sat in one seven-session cell.
          </p>
        </HelpWarn>
        <HelpSection title="The grid">
          <p>
            Colour is {rateLabel} rate (darker = higher); the cell prints sessions and {rateLabel} %. Rows are {tab.dims[0]}, columns{" "}
            {tab.dims[1]}. A grey cell is one the writer flagged thin (fewer than {thinBelowSessions ?? "the writer's floor of"} sessions);{" "}
            <em>fragile</em> and <em>±n</em> (sign changes over prior snapshots) are the writer&apos;s stamps. Hover for trades, completed,
            win and avg.
          </p>
        </HelpSection>
      </>
    ),
  };
}

/**
 * The dimension's same-day comparisons, as the writer listed them: bucket a against bucket b on the
 * sessions both traded, per trade. A row at or above the writer's alpha is dimmed, never hidden;
 * with no alpha on the artifact nothing is dimmed, rather than using a console copy of the bar.
 */
function pairedRows(cuts: RegimeCuts, dim: string): Array<{ arm: string; p: RegimePair }> {
  const rows: Array<{ arm: string; p: RegimePair }> = [];
  for (const b of cuts.arms) for (const p of b.dimensions[dim]?.paired ?? []) rows.push({ arm: b.arm, p });
  return rows;
}

function PairedTable({ cuts, dim }: { cuts: RegimeCuts; dim: string }) {
  const rows = pairedRows(cuts, dim);
  const sort = useSort();
  if (rows.length === 0) return null;
  const alpha = cuts.multiplicity?.pairedAlpha ?? null;
  // W–L sorts by the margin (a's days less b's), the number the cell is read for.
  const sorted = sortRows(rows, sort, {
    arm: (r) => r.arm,
    wl: (r) => r.p.aBetterSessions - r.p.bBetterSessions,
    diff: (r) => r.p.meanDiffPerTrade,
    p: (r) => r.p.signTestP,
  });
  return (
    <>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        Two buckets on the same days, per trade: a bucket that wins the by-regime table but not here is a kind of day,
        not a kind of entry. Tied days are left out of W–L, and pairs sharing too few sessions are omitted by the
        writer.{alpha !== null && ` Rows at p ≥ ${alpha} are dimmed.`}
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="arm" k="arm" sort={sort} />
              <th>a vs b</th>
              <SortTh
                label="W–L of days"
                k="wl"
                sort={sort}
                title="Sessions a beat b per trade – sessions b beat a, of the sessions both traded (sorts by the margin)"
              />
              <SortTh
                label="per trade"
                k="diff"
                sort={sort}
                title="Mean per-trade net difference, a minus b, over the shared sessions (hover a value for the median)"
              />
              <SortTh label="p" k="p" sort={sort} title="Exact two-sided sign test" />
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ arm, p }) => {
              const weak = alpha !== null && (p.signTestP === null || p.signTestP >= alpha);
              return (
                <tr key={`${arm}-${p.a}-${p.b}`} style={weak ? { opacity: 0.55 } : undefined}>
                  <td>{arm}</td>
                  <td>
                    {p.a} vs {p.b}
                  </td>
                  <td>
                    {p.aBetterSessions}–{p.bBetterSessions} of {p.sessions}
                  </td>
                  <td title={p.medianDiffPerTrade === null ? undefined : `median ${fmtMoney(p.medianDiffPerTrade)}`}>
                    <PnlCell v={p.meanDiffPerTrade} />
                  </td>
                  <td>{p.signTestP === null ? "—" : p.signTestP.toFixed(p.signTestP < 0.01 ? 3 : 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** A dimension card's ?: how to read a cell and the writer's stamps, with undersampling leading as a warning. */
function dimensionHelp(cuts: RegimeCuts, dim: string): CardHelpContent {
  const rateLabel = cuts.arms.some((b) => b.completionRate !== null) ? "completion" : "win";
  const under = cuts.arms.filter((b) => b.dimensions[dim]?.underpowered === true).map((b) => b.arm);
  return {
    title: `By ${dim}`,
    body: (
      <>
        {under.length > 0 && (
          <HelpWarn title="Underpowered">
            <p>
              {under.join(", ")} {under.length === 1 ? "has" : "have"} too few sessions in this dimension to read; the suffix beside the arm
              says how many.
            </p>
          </HelpWarn>
        )}
        <HelpSection title="Reading a cell">
          <p>
            Each cell reads {rateLabel} % · net · sessions. A dash means no rows in that bucket for that arm. A dimmed cell is one the writer
            flagged thin (fewer than {cuts.thinBelowSessions ?? "the writer's floor of"} sessions); an arm&apos;s suffix is the writer&apos;s
            coverage and power reading for this dimension. <em>fragile</em> marks a cell one session dominates or whose sign one session
            decides; <em>±n</em> one whose net changed sign over the prior snapshots.
          </p>
        </HelpSection>
      </>
    ),
  };
}

function DimensionCard({ cuts, dim, lb }: { cuts: RegimeCuts; dim: string; lb: OpenParam }) {
  const columns = bucketColumns(cuts.arms, dim);
  const under = cuts.arms.some((b) => b.dimensions[dim]?.underpowered === true);
  return (
    <Card
      title={`by ${dim}`}
      className="lv-12"
      collapseKey={`regime-${cuts.module}-${dim}`}
      help={dimensionHelp(cuts, dim)}
      flag={under ? "underpowered" : undefined}
    >
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th></th>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cuts.arms.map((b) => {
              const d = b.dimensions[dim];
              const byBucket = new Map((d?.buckets ?? []).map((c) => [c.bucket, c] as const));
              const flags = d
                ? [
                    d.coveragePct !== null && d.coveragePct < 100 ? `cov ${d.coveragePct.toFixed(0)}%` : null,
                    d.underpowered ? `${d.sessions} sessions · underpowered` : null,
                    d.degenerate ? "degenerate" : null,
                  ].filter((f): f is string => f !== null)
                : ["no rows"];
              return (
                <tr key={b.arm}>
                  <td>
                    {b.arm}
                    {flags.length > 0 && (
                      <span className="muted" style={{ fontSize: 10, marginLeft: 6 }}>
                        {flags.join(" · ")}
                      </span>
                    )}
                  </td>
                  {columns.map((col) => {
                    const c = byBucket.get(col);
                    if (c === undefined || c.trades === 0) return <td key={col} className="muted">—</td>;
                    return (
                      <td key={col} style={c.thin ? { opacity: 0.55 } : undefined} title={cellTitle(c)}>
                        {cellText(c)}
                        {c.thin && <ThinMark />}
                        <StampMarks c={c} />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pairedRows(cuts, dim).length > 0 && (
        <div className="pf-mores">
          <BoxOpen lb={lb} id={pairedBoxId(dim)}>
            same-day comparisons
          </BoxOpen>
        </div>
      )}
    </Card>
  );
}

const pairedBoxId = (dim: string): string => `paired-${dim}`;

/** The lightbox over a dimension's same-day comparisons, one per dimension that has any. */
export function pairedBoxes(cuts: RegimeCuts): FliesBox[] {
  return cuts.dimensions
    .filter((dim) => pairedRows(cuts, dim).length > 0)
    .map((dim) => ({
      id: pairedBoxId(dim),
      kicker: `by ${dim} ›`,
      title: "Same-day comparisons",
      tabs: [{ id: "paired", label: "same-day comparisons" }],
      body: () => <PairedTable cuts={cuts} dim={dim} />,
    }));
}

function CrossTabCard({ cuts, tab }: { cuts: RegimeCuts; tab: RegimeCrossTab }) {
  const thin = tab.arms.some((b) => b.cells.some((c) => c.thin));
  return (
    <Card
      title={`by ${tab.dims.join(" × ")}`}
      className="lv-12"
      collapseKey={`regime-${cuts.module}-cross-${tab.dims.join("-")}`}
      help={crossTabHelp(tab, cuts.thinBelowSessions)}
      flag={thin ? "thin cells" : undefined}
    >
      <CrossTabGrid tab={tab} />
    </Card>
  );
}

/** The writer's chance baseline for the whole document, printed as one line. */
function MultiplicityLine({ m }: { m: RegimeMultiplicity }) {
  const bar = m.pairedAlpha === null ? "the writer's bar" : `p ${m.pairedAlpha}`;
  const chance = (v: number | null): string => (v === null ? "" : `; about ${v} expected by chance`);
  return (
    <p className="muted" style={{ margin: "0.4rem 0 0" }}>
      {m.pairedBelowAlpha} of {m.pairedTests} same-day comparisons below {bar}
      {chance(m.pairedExpectedByChance)}. {m.intervalsExcludingZero} of {m.intervals} cell intervals exclude zero
      {chance(m.intervalsExpectedByChance)}.
    </p>
  );
}

export function EraCard({ cuts, stale }: { cuts: RegimeCuts; stale: { artifactSession: string; latestSession: string } | null }) {
  const own = cuts.arms.filter((b) => b.eraStart !== null && b.eraStart !== cuts.era.start);
  const sort = useSort();
  const arms = sortRows(cuts.arms, sort, {
    arm: (b) => b.arm,
    from: (b) => b.eraStart,
    sessions: (b) => b.sessions,
    trades: (b) => b.trades,
    rate: (b) => b.completionRate ?? b.winRate,
    net: (b) => b.netPnl,
  });
  return (
    <Card title="era" collapseKey={`regime-${cuts.module}-era`} help={ERA_HELP}>
      <p style={{ marginTop: 0 }}>
        era since <strong>{cuts.era.start ?? "the first row"}</strong>
        {cuts.era.boundingBreak && <span className="muted"> · {cuts.era.boundingBreak.kind}</span>}
        {cuts.era.key && <span className="muted"> · era column {cuts.era.key}</span>}
        {" · "}
        session {cuts.session ?? "?"}
        {cuts.symbol && ` · ${cuts.symbol}`}
        {cuts.entryModes && ` · ${cuts.entryModes.join(", ")} entries`}
      </p>
      {stale !== null && (
        <p className="pnl-neg" style={{ marginTop: 0 }}>
          stale: this artifact is for {stale.artifactSession}; the ledger holds {stale.latestSession}. The nightly
          job has not run since.
        </p>
      )}
      {own.length > 0 && (
        <p className="muted" style={{ marginTop: 0 }}>
          own start: {own.map((b) => `${b.arm} from ${b.eraStart}${b.eraBreak ? ` (${b.eraBreak.kind})` : ""}`).join("; ")}
        </p>
      )}
      {cuts.era.ignoredFuture.length > 0 && (
        <p className="muted" style={{ marginTop: 0 }}>
          declared, not yet binding: {cuts.era.ignoredFuture.map((b) => `${b.breakDate} ${b.scope} ${b.kind}`).join("; ")}
        </p>
      )}
      {cuts.era.caveats.length > 0 && (
        <p className="muted" style={{ marginTop: 0 }}>
          caveats inside the era: {cuts.era.caveats.map((b) => `${b.breakDate} ${b.kind}${b.reason ? ` — ${b.reason}` : ""}`).join("; ")}
        </p>
      )}
      <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="arm" k="arm" sort={sort} />
            <SortTh label="from" k="from" sort={sort} />
            <SortTh label="sessions" k="sessions" sort={sort} />
            <SortTh label="trades" k="trades" sort={sort} />
            <SortTh label={cuts.arms.some((b) => b.completionRate !== null) ? "completion" : "win"} k="rate" sort={sort} />
            <SortTh label="net" k="net" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {arms.map((b) => (
            <tr key={b.arm} style={b.thin === true ? { opacity: 0.55 } : undefined}>
              <td>{b.arm}</td>
              <td>{b.eraStart ?? "—"}</td>
              <td>{b.sessions}</td>
              <td>{b.trades}</td>
              <td>{b.completionRate !== null ? pct(b.completionRate) : pct(b.winRate)}</td>
              <td>
                <PnlCell v={b.netPnl} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </Card>
  );
}

/**
 * The cut narrowed to one arm, for the page's arm picker: its row in every table, its cells in every
 * cross-tab. The document-level stamps (era, multiplicity) are the writer's for the whole cut and
 * are left as they are; the tab says so when an arm is picked.
 */
export function cutsForArm(cuts: RegimeCuts, arm: string | null): RegimeCuts {
  if (arm === null) return cuts;
  return {
    ...cuts,
    arms: cuts.arms.filter((a) => a.arm === arm),
    crossTabs: cuts.crossTabs.map((t) => ({ ...t, arms: t.arms.filter((a) => a.arm === arm) })),
  };
}

const REGIME_HELP: CardHelpContent = {
  title: "Regime cuts",
  body: (
    <>
      <HelpSection title="What this is">
        <p>
          The module&apos;s own nightly artifact, cut by the regime each entry was tagged with, rendered as written. The console computes
          nothing from it: thin cells, coverage, era and every number are the writer&apos;s.
        </p>
      </HelpSection>
      <HelpSection title="Read sessions first">
        <p>Sessions are the unit of independence; read them before the net. Same-day entries share a regime.</p>
      </HelpSection>
    </>
  ),
};

const ERA_HELP: CardHelpContent = {
  title: "Era",
  body: (
    <HelpSection title="The window">
      <p>
        Every cut below is over the era the writer stamped: since its start, by the era column where there is one. An arm with its own start
        is listed; breaks declared but not yet binding, and caveats inside the era, are the writer&apos;s notes.
      </p>
    </HelpSection>
  ),
};

/**
 * The session the cut is read for, behind filter & sort: one choice, the nightly artifact of that day
 * (the latest by default). Returns the button for the card head and the summary and panel for its body.
 */
function useSessionFilter(sessions: string[], session: string | null, setSession: (s: string | null) => void) {
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const changed = session !== null ? 1 : 0;
  if (sessions.length <= 1) return { button: null, body: null };
  const reset = () => setSession(null);
  return {
    button: <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />,
    body: (
      <>
        <FilterSummary parts={[session === null ? "latest session" : `session ${session}`]} changed={changed} onOpen={() => setPanel(true)} onReset={reset} />
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          <FilterGroup title="session" note="the artifact the module wrote that night">
            <select
              className="chip review-session-select"
              value={session ?? ""}
              onChange={(e) => setSession(e.target.value === "" ? null : e.target.value)}
              aria-label="session"
            >
              <option value="">latest</option>
              {[...sessions].reverse().map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </FilterGroup>
        </FilterSortPanel>
      </>
    ),
  };
}

export function RegimeCutsTab({ module, arm = null }: { module: RegimeCutsModule; arm?: string | null }) {
  const [session, setSession] = useState<string | null>(null);
  const { data, isLoading, isError, dataUpdatedAt } = useRegimeCuts(module, session);
  const lb = useOpenParam("reg");
  const filter = useSessionFilter(data?.sessions ?? [], session, setSession);
  const frame = (card: ReactNode) => (
    <div className="pf-layout">
      <Section concept="stand" tag="the cut" question="What does this cut cover?">
        {card}
      </Section>
    </div>
  );
  if (isLoading || data === undefined) {
    return frame(
      <Card title="regime cuts" updatedAt={dataUpdatedAt} isError={isError}>
        <div className="table-scroll">
          <table className="data-table">
            <tbody>
              <SkeletonRows n={4} cols={5} />
            </tbody>
          </table>
        </div>
      </Card>,
    );
  }
  if (data.status === "absent") {
    return frame(
      <Card title="regime cuts" updatedAt={dataUpdatedAt} controls={filter.button}>
        {filter.body}
        <p style={{ marginTop: 0 }}>No regime-cuts artifact yet.</p>
        <p className="muted" style={{ marginBottom: 0 }}>
          The module writes it nightly at 16:40 ET (<code>{WRITE_COMMAND[module]}</code>, which also accepts{" "}
          <code>--session YYYY-MM-DD</code> for a past day). Looked for <code>{data.path}</code>.
        </p>
      </Card>,
    );
  }
  if (data.status === "failed") {
    return frame(
      <Card title="regime cuts" updatedAt={dataUpdatedAt} isError controls={filter.button}>
        {filter.body}
        <p className="pnl-neg" style={{ marginTop: 0 }}>
          The artifact could not be read: {data.error}
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          This is a failure, not an empty day. Re-run <code>{WRITE_COMMAND[module]}</code> and check the file.
        </p>
      </Card>,
    );
  }
  const { stale } = data;
  const cuts = cutsForArm(data.cuts, arm);
  const boxes = pairedBoxes(cuts);
  return (
    <div className="pf-layout view-fade">
      <Section concept="stand" tag="the cut" question="What does this cut cover?">
        <Card
          title="regime cuts"
          updatedAt={dataUpdatedAt}
          controls={filter.button}
          collapseKey={`regime-${module}-head`}
          help={REGIME_HELP}
          flag={stale !== null ? "stale" : undefined}
        >
          {filter.body}
          <p className="muted" style={{ margin: 0 }}>
            {arm === null ? "Every arm" : <strong>{arm}</strong>}, generated {cuts.generatedAt ?? "?"}.
          </p>
          {arm !== null && cuts.arms.length === 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>
              {arm} is not in this cut. Pick another arm, or all arms, in the header.
            </p>
          )}
          {cuts.multiplicity !== null && (
            <>
              {arm !== null && (
                <p className="muted" style={{ fontSize: 11, marginBottom: 0 }}>
                  the chance baseline below counts the whole cut, every arm, not only {arm}
                </p>
              )}
              <MultiplicityLine m={cuts.multiplicity} />
            </>
          )}
        </Card>
        <EraCard cuts={cuts} stale={stale} />
      </Section>
      {cuts.dimensions.length > 0 && (
        <Section concept="edge" tag="by regime" question="Does the edge depend on the regime?">
          {cuts.dimensions.map((dim) => (
            <DimensionCard key={dim} cuts={cuts} dim={dim} lb={lb} />
          ))}
        </Section>
      )}
      {cuts.crossTabs.length > 0 && (
        <Section concept="risk" tag="cross-tabs" question="Does one regime hide the effect of another?">
          {cuts.crossTabs.map((tab) => (
            <CrossTabCard key={tab.dims.join("-")} cuts={cuts} tab={tab} />
          ))}
        </Section>
      )}
      <FliesBoxHost lb={lb} boxes={boxes} />
    </div>
  );
}
