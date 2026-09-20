import { useState } from "react";
import type { RegimeBook, RegimeCell, RegimeCrossCell, RegimeCuts, RegimeCutsModule } from "@console/shared";
import { useRegimeCuts } from "../lib/api";
import { Card, PnlCell, SkeletonRows, fmtMoney } from "./DataTable";

/**
 * The regime-cuts slide (2026-09-19): the module's own nightly artifact, one table per regime
 * dimension (rows = books, columns = the buckets that occur) and one for the declared cross-tab,
 * rendered as written. This component derives nothing: `thin` is the writer's flag, the era and
 * every number come off the file, and the only layout decision made here is which columns to
 * draw, from the buckets present. Absent, failed and stale are shown as three different things.
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

function bucketColumns(books: RegimeBook[], dim: string): string[] {
  // Union of buckets across books, ordered by total trades desc, untagged/unknown last -- the one
  // layout decision this slide makes. The writer already orders each book's own buckets that way.
  const totals = new Map<string, number>();
  for (const b of books) for (const c of b.dimensions[dim]?.buckets ?? []) totals.set(c.bucket, (totals.get(c.bucket) ?? 0) + c.trades);
  const last = (k: string): number => (k === "untagged" || k === "unknown" ? 1 : 0);
  return [...totals.entries()].sort((a, b) => last(a[0]) - last(b[0]) || b[1] - a[1] || a[0].localeCompare(b[0])).map((e) => e[0]);
}

function DimensionCard({ cuts, dim }: { cuts: RegimeCuts; dim: string }) {
  const columns = bucketColumns(cuts.books, dim);
  const rateLabel = cuts.books.some((b) => b.completionRate !== null) ? "completion" : "win";
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
            {cuts.books.map((b) => {
              const d = b.dimensions[dim];
              const byBucket = new Map((d?.buckets ?? []).map((c) => [c.bucket, c] as const));
              const flags = d
                ? [
                    d.coveragePct !== null && d.coveragePct < 100 ? `cov ${d.coveragePct.toFixed(0)}%` : null,
                    d.underpowered ? `n=${d.effectiveN} underpowered` : null,
                    d.degenerate ? "degenerate" : null,
                  ].filter((f): f is string => f !== null)
                : ["no rows"];
              return (
                <tr key={b.book}>
                  <td>
                    {b.book}
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
        Each cell reads {rateLabel} % · net · sessions. A dash means no rows in that bucket for that book. A dimmed
        cell is one the writer flagged thin (fewer than {cuts.thinBelowSessions} sessions); a book's suffix is the
        writer's coverage and power reading for this dimension.
      </p>
    </Card>
  );
}

function CrossTabCard({ cuts }: { cuts: RegimeCuts }) {
  const tab = cuts.crossTabs[0];
  if (tab === undefined) return null;
  const totals = new Map<string, number>();
  for (const b of tab.books) for (const c of b.cells) totals.set(c.buckets.join(" / "), (totals.get(c.buckets.join(" / ")) ?? 0) + c.trades);
  const columns = [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map((e) => e[0]);
  return (
    <Card title={`by ${tab.dims.join(" × ")}`} collapseKey={`regime-${cuts.module}-cross`}>
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
            {tab.books.map((b) => {
              const byKey = new Map(b.cells.map((c) => [c.buckets.join(" / "), c] as const));
              return (
                <tr key={b.book}>
                  <td>{b.book}</td>
                  {columns.map((col) => {
                    const c = byKey.get(col);
                    if (c === undefined || c.trades === 0) return <td key={col} className="muted">—</td>;
                    return (
                      <td key={col} style={c.thin ? { opacity: 0.55 } : undefined} title={cellTitle(c)}>
                        {cellText(c)}
                        {c.thin && <ThinMark />}
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
        The one declared cross-tab. It exists because a single-dimension cut once made net-GEX sign look predictive
        until the trend bucket showed the whole effect sat in one seven-session cell. Read sessions first.
      </p>
    </Card>
  );
}

function EraCard({ cuts, stale }: { cuts: RegimeCuts; stale: { artifactSession: string; latestSession: string } | null }) {
  const own = cuts.books.filter((b) => b.eraStart !== null && b.eraStart !== cuts.era.start);
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
          own start: {own.map((b) => `${b.book} from ${b.eraStart}${b.eraBreak ? ` (${b.eraBreak.kind})` : ""}`).join("; ")}
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
            <th>{cuts.books.some((b) => b.completionRate !== null) ? "completion" : "win"}</th>
            <th>net</th>
          </tr>
        </thead>
        <tbody>
          {cuts.books.map((b) => (
            <tr key={b.book} style={b.sessions < cuts.thinBelowSessions ? { opacity: 0.55 } : undefined}>
              <td>{b.book}</td>
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
          Every book, cut by the regime each entry was tagged with, as the module wrote it (generated {cuts.generatedAt ?? "?"}).
          The console renders this artifact and computes nothing from it: thin cells, coverage, era and every number
          are the writer's. Sessions are the unit of independence; read them before the net.
        </p>
      </Card>
      <EraCard cuts={cuts} stale={stale} />
      {cuts.dimensions.map((dim) => (
        <DimensionCard key={dim} cuts={cuts} dim={dim} />
      ))}
      <CrossTabCard cuts={cuts} />
    </div>
  );
}
