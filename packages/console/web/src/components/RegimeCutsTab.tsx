import { Fragment, useState } from "react";
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
import { Card, PnlCell, SkeletonRows, fmtMoney } from "./DataTable";

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

export function CrossTabGrid({ tab, thinBelowSessions }: { tab: RegimeCrossTab; thinBelowSessions: number | null }) {
  const rows = crossAxis(tab, 0);
  const cols = crossAxis(tab, 1);
  const rateLabel = tab.arms.some((b) => b.cells.some((c) => c.completionRate !== null)) ? "completion" : "win";
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
                          ? "var(--row-line)"
                          : `rgba(67, 181, 122, ${(0.15 + 0.85 * rate).toFixed(3)})`;
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
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        Colour is {rateLabel} rate (darker = higher); the cell prints sessions and {rateLabel} %. Rows are{" "}
        {tab.dims[0]}, columns {tab.dims[1]}. A grey cell is one the writer flagged thin (fewer than{" "}
        {thinBelowSessions ?? "the writer's floor of"} sessions); <em>fragile</em> and <em>±n</em> (sign changes over
        prior snapshots) are the writer's stamps. Hover for trades, completed, win and avg.
      </p>
    </>
  );
}

/**
 * The dimension's same-day comparisons, as the writer listed them: bucket a against bucket b on the
 * sessions both traded, per trade. A row at or above the writer's alpha is dimmed, never hidden;
 * with no alpha on the artifact nothing is dimmed, rather than using a console copy of the bar.
 */
function PairedTable({ cuts, dim }: { cuts: RegimeCuts; dim: string }) {
  const rows: Array<{ arm: string; p: RegimePair }> = [];
  for (const b of cuts.arms) for (const p of b.dimensions[dim]?.paired ?? []) rows.push({ arm: b.arm, p });
  if (rows.length === 0) return null;
  const alpha = cuts.multiplicity?.pairedAlpha ?? null;
  return (
    <>
      <h3 style={{ fontSize: 12, margin: "0.9rem 0 0.3rem" }}>same-day comparisons</h3>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th></th>
              <th>a vs b</th>
              <th title="Sessions a beat b per trade – sessions b beat a, of the sessions both traded">W–L of days</th>
              <th title="Mean per-trade net difference, a minus b, over the shared sessions (hover a value for the median)">
                per trade
              </th>
              <th title="Exact two-sided sign test">p</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ arm, p }) => {
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
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        Two buckets on the same days, per trade: a bucket that wins the table above but not here is a kind of day,
        not a kind of entry. Tied days are left out of W–L, and pairs sharing too few sessions are omitted by the
        writer.{alpha !== null && ` Rows at p ≥ ${alpha} are dimmed.`}
      </p>
    </>
  );
}

function DimensionCard({ cuts, dim }: { cuts: RegimeCuts; dim: string }) {
  const columns = bucketColumns(cuts.arms, dim);
  const rateLabel = cuts.arms.some((b) => b.completionRate !== null) ? "completion" : "win";
  return (
    <Card title={`by ${dim}`} collapseKey={`regime-${cuts.module}-${dim}`}>
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
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        Each cell reads {rateLabel} % · net · sessions. A dash means no rows in that bucket for that arm. A dimmed
        cell is one the writer flagged thin (fewer than {cuts.thinBelowSessions ?? "the writer's floor of"} sessions); an arm's suffix is the
        writer's coverage and power reading for this dimension. <em>fragile</em> marks a cell one session dominates or
        whose sign one session decides; <em>±n</em> one whose net changed sign over the prior snapshots.
      </p>
      <PairedTable cuts={cuts} dim={dim} />
    </Card>
  );
}

function CrossTabCard({ cuts, tab }: { cuts: RegimeCuts; tab: RegimeCrossTab }) {
  return (
    <Card title={`by ${tab.dims.join(" × ")}`} collapseKey={`regime-${cuts.module}-cross-${tab.dims.join("-")}`}>
      <CrossTabGrid tab={tab} thinBelowSessions={cuts.thinBelowSessions} />
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        A declared cross-tab. The first exists because a single-dimension cut once made net-GEX sign look predictive
        until the trend bucket showed the whole effect sat in one seven-session cell. Read sessions first.
      </p>
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

function EraCard({ cuts, stale }: { cuts: RegimeCuts; stale: { artifactSession: string; latestSession: string } | null }) {
  const own = cuts.arms.filter((b) => b.eraStart !== null && b.eraStart !== cuts.era.start);
  return (
    <Card title="era" collapseKey={`regime-${cuts.module}-era`}>
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
      <table className="data-table">
        <thead>
          <tr>
            <th></th>
            <th>from</th>
            <th>sessions</th>
            <th>trades</th>
            <th>{cuts.arms.some((b) => b.completionRate !== null) ? "completion" : "win"}</th>
            <th>net</th>
          </tr>
        </thead>
        <tbody>
          {cuts.arms.map((b) => (
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
    </Card>
  );
}

export function RegimeCutsTab({ module }: { module: RegimeCutsModule }) {
  const [session, setSession] = useState<string | null>(null);
  const { data, isLoading, isError, dataUpdatedAt } = useRegimeCuts(module, session);
  if (isLoading || data === undefined) {
    return (
      <Card title="regime cuts" updatedAt={dataUpdatedAt} isError={isError}>
        <table className="data-table">
          <tbody>
            <SkeletonRows n={4} cols={5} />
          </tbody>
        </table>
      </Card>
    );
  }
  const picker =
    data.sessions.length > 1 ? (
      <select value={session ?? ""} onChange={(e) => setSession(e.target.value === "" ? null : e.target.value)}>
        <option value="">latest</option>
        {[...data.sessions].reverse().map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    ) : null;
  if (data.status === "absent") {
    return (
      <Card title="regime cuts" updatedAt={dataUpdatedAt} controls={picker}>
        <p style={{ marginTop: 0 }}>No regime-cuts artifact yet.</p>
        <p className="muted" style={{ marginBottom: 0 }}>
          The module writes it nightly at 16:40 ET (<code>{WRITE_COMMAND[module]}</code>, which also accepts{" "}
          <code>--session YYYY-MM-DD</code> for a past day). Looked for <code>{data.path}</code>.
        </p>
      </Card>
    );
  }
  if (data.status === "failed") {
    return (
      <Card title="regime cuts" updatedAt={dataUpdatedAt} isError controls={picker}>
        <p className="pnl-neg" style={{ marginTop: 0 }}>
          The artifact could not be read: {data.error}
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          This is a failure, not an empty day. Re-run <code>{WRITE_COMMAND[module]}</code> and check the file.
        </p>
      </Card>
    );
  }
  const { cuts, stale } = data;
  return (
    <div className="cards cards-wide view-fade">
      <Card title="regime cuts" updatedAt={dataUpdatedAt} controls={picker} collapseKey={`regime-${module}-head`}>
        <p className="muted" style={{ margin: 0 }}>
          Every arm, cut by the regime each entry was tagged with, as the module wrote it (generated {cuts.generatedAt ?? "?"}).
          The console renders this artifact and computes nothing from it: thin cells, coverage, era and every number
          are the writer's. Sessions are the unit of independence; read them before the net.
        </p>
        {cuts.multiplicity !== null && <MultiplicityLine m={cuts.multiplicity} />}
      </Card>
      <EraCard cuts={cuts} stale={stale} />
      {cuts.dimensions.map((dim) => (
        <DimensionCard key={dim} cuts={cuts} dim={dim} />
      ))}
      {cuts.crossTabs.map((tab) => (
        <CrossTabCard key={tab.dims.join("-")} cuts={cuts} tab={tab} />
      ))}
    </div>
  );
}
