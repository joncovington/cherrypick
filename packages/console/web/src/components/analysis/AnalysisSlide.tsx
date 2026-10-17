import type { ArmAnalysis, AnalysisStreaks, CapitalReading, DrawdownEpisode, TradingMode } from "@console/shared";
import { Card } from "../DataTable";
import { fmtMoney, fmtNum, fmtPct } from "../../lib/format";
import { useModuleAnalysis, type PerformanceModuleId } from "../../lib/api";
import { Tile } from "../performance/Tile";
import { TileGrid } from "../performance/TileGrid";

/**
 * The strategy-analysis slide: per arm, what the headline reading does not say
 * (docs/strategy-analysis-plan.md). Generic over every module `GET /api/analysis/:module` reads, as
 * `PerformanceSlide` is, and read over the same ledger and window.
 *
 * Presentation only. Every figure comes off `core.analysis` through the bridge; the only arithmetic
 * here is a sign flip to show a drawdown as the loss it is. Tone comes from a number's sign, never
 * a threshold, and a null is an em dash.
 */

function tone(v: number | null): "pos" | "neg" | "dim" {
  if (v === null) return "dim";
  return v >= 0 ? "pos" : "neg";
}

/** A drawdown is a fall, stored as a positive depth: shown as the loss it is. Zero stays zero. */
function loss(depth: number | null): number | null {
  return depth === null ? null : depth === 0 ? 0 : -depth;
}

function lossTone(depth: number | null): "neg" | "dim" {
  return depth === null || depth === 0 ? "dim" : "neg";
}

function run(s: AnalysisStreaks): string {
  return s.current === null ? "—" : `${String(s.current.length)} ${s.current.kind === "win" ? "up" : "down"}`;
}

function span(sessions: number | null, days: number | null): string {
  if (sessions === null) return "—";
  return `${String(sessions)} ses${days === null ? "" : ` · ${String(days)}d`}`;
}

/** A fraction from core (0.31) as a percent (31.0%). */
function pct(v: number | null): string {
  return fmtPct(v === null ? null : v * 100, 1);
}

/**
 * Capital, two ways (core.analysis.capital): the risk the arm was observed to carry at once, and the
 * allocation a person declared for it. Six tiles, so the row never comes out ragged.
 */
export function CapitalTiles({ c }: { c: CapitalReading }) {
  const p = c.peak_risk;
  const a = c.allocation;
  const peakTitle =
    p.withheld !== null
      ? p.withheld
      : `on ${p.session ?? "—"}${p.at !== null ? ` at ${p.at.slice(11, 16)}` : ""}; ` +
        (p.basis === "intraday"
          ? "summed where positions' entry and exit times overlap"
          : p.basis === "mixed"
            ? "positions open across sessions, plus same-session positions where their times overlap"
            : "positions summed over every session they were open") +
        (p.untimed > 0 ? `; ${String(p.untimed)} without times counted as open all session` : "");
  const declared = a.current !== null;
  return (
    <>
      <TileGrid count={6}>
        <Tile
          label="peak risk"
          value={fmtMoney(p.peak)}
          tone="dim"
          n={p.capital_known}
          title={peakTitle}
        />
        <Tile
          label="net / largest peak"
          value={pct(c.return_on_peak_risk)}
          tone={tone(c.return_on_peak_risk)}
          afterFees
          title={
            "the arm's whole net over the most it ever had at risk at once. Not the performance slide's " +
            "\"return on peak risk\", which divides by every session's peak summed and so reads per day of risk"
          }
        />
        <Tile
          label="max drawdown / peak risk"
          value={pct(c.max_drawdown_over_peak_risk)}
          tone={lossTone(c.max_drawdown_over_peak_risk)}
          title="the session max drawdown as a share of the most the arm had at risk at once"
        />
        <Tile
          label="allocation"
          value={declared ? fmtMoney(a.current) : "none declared"}
          tone="dim"
          title={
            declared
              ? a.declared.map((d) => `${fmtMoney(d.capital)} from ${d.from}${d.note ? ` (${d.note})` : ""}`).join("; ")
              : "no allocation is declared for this arm in arm_capital.json"
          }
        />
        <Tile
          label="return on allocation"
          value={pct(a.return)}
          tone={tone(a.return)}
          n={declared ? a.covered : null}
          nUnit="sessions"
          afterFees={declared}
        />
        <Tile
          label="allocation / max drawdown"
          value={a.capital_over_max_drawdown === null ? "—" : `${fmtNum(a.capital_over_max_drawdown, 1)}×`}
          tone="dim"
          title="the latest declared allocation over the dollar max drawdown of the sessions it covers"
        />
      </TileGrid>
      {p.withheld !== null && (
        <p className="muted" style={{ fontSize: 11 }}>
          peak risk unknown — {p.withheld}
        </p>
      )}
      {p.withheld === null && p.capital_known < p.trades && (
        <p className="muted" style={{ fontSize: 11 }}>
          capital recorded on {String(p.capital_known)} of {String(p.trades)} trades: the peak is over those, and the
          ratios beside it are withheld
        </p>
      )}
      {declared && a.uncovered > 0 && (
        <p className="muted" style={{ fontSize: 11 }}>
          {String(a.uncovered)} of {String(a.sessions)} sessions come before the first declared allocation and join
          neither allocation figure
        </p>
      )}
    </>
  );
}

function EpisodesTable({ episodes }: { episodes: DrawdownEpisode[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>high</th>
            <th>low</th>
            <th>new high</th>
            <th>depth</th>
            <th title="sessions traded from the high to the low, and calendar days">falling</th>
            <th title="sessions traded from the low to the new high, and calendar days">recovering</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((e) => (
            <tr key={`${e.peak_session ?? "start"}-${e.low_session}`}>
              <td>{e.peak_session ?? "start"}</td>
              <td>{e.low_session}</td>
              <td className={e.open ? "muted" : undefined}>{e.recovered_session ?? "not yet"}</td>
              <td className="pnl-neg">{fmtMoney(loss(e.depth))}</td>
              <td>{span(e.sessions_falling, e.days_falling)}</td>
              <td>{span(e.sessions_recovering, e.days_recovering)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DurabilityTable({ d }: { d: ArmAnalysis["durability"] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>trades</th>
            <th title="winning trades removed, of the number the share asks for">winners removed</th>
            <th>their net</th>
            <th>net without them</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>all {String(d.trades)}</td>
            <td>—</td>
            <td>—</td>
            <td className={`pnl-${d.net >= 0 ? "pos" : "neg"}`}>{fmtMoney(d.net)}</td>
          </tr>
          {d.without_top.map((r) => (
            <tr key={r.fraction}>
              <td>without the top {String(Math.round(r.fraction * 100))}%</td>
              <td>
                {String(r.removed)}
                {r.removed < r.asked && <span className="muted"> of {String(r.asked)}</span>}
              </td>
              <td>{fmtMoney(r.removed_net)}</td>
              <td className={`pnl-${r.net >= 0 ? "pos" : "neg"}`}>
                {fmtMoney(r.net)}
                {r.sign_flips && (
                  <span className="chip chip-warn" style={{ marginLeft: 6, fontSize: 9 }}>
                    sign flips
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArmCard({ module, tag, a, updatedAt }: { module: PerformanceModuleId; tag: string; a: ArmAnalysis; updatedAt: number }) {
  const s = a.stress;
  const dd = a.drawdowns;
  const deepest = dd.deepest[0]?.depth ?? null;
  const cov = a.coverage;
  const stressTitle =
    s.withheld !== null
      ? `withheld: ${s.withheld}`
      : `the ${String(Math.round(s.quantile * 100))}th percentile of ${String(s.resamples)} reorderings of the same ${String(s.sessions)} sessions`;
  return (
    <Card title={tag} collapseKey={`analysis-${module}-${tag}`} updatedAt={updatedAt}>
      {/* Twelve tiles: runs and extremes, then drawdowns and the stress test. */}
      <TileGrid count={12}>
        <Tile label="longest up run" value={String(a.session_streaks.longest_win)} tone="dim" nUnit="sessions" n={s.sessions} />
        <Tile label="longest down run" value={String(a.session_streaks.longest_loss)} tone="dim" nUnit="sessions" n={s.sessions} />
        <Tile label="current run" value={run(a.session_streaks)} tone="dim" title="sessions in a row up or down, as the series ends" />
        <Tile
          label="trade runs"
          value={`${String(a.streaks.longest_win)} W / ${String(a.streaks.longest_loss)} L`}
          tone="dim"
          n={cov.trades}
          title="longest runs counted trade by trade; where a session holds several positions the session runs are the ones that say how many days in a row"
        />
        <Tile label="best trade" value={fmtMoney(a.best_trade?.net ?? null)} tone={tone(a.best_trade?.net ?? null)} afterFees title={a.best_trade?.session ?? undefined} />
        <Tile label="worst trade" value={fmtMoney(a.worst_trade?.net ?? null)} tone={tone(a.worst_trade?.net ?? null)} afterFees title={a.worst_trade?.session ?? undefined} />
        <Tile label="drawdowns" value={String(dd.count)} tone="dim" title="falls below a previous high, by session" />
        <Tile label="deepest (session)" value={fmtMoney(loss(deepest))} tone={lossTone(deepest)} afterFees />
        <Tile
          label="open drawdown"
          value={dd.open === null ? "none" : fmtMoney(loss(dd.open.depth))}
          tone={dd.open === null ? "dim" : lossTone(dd.open.depth)}
          title={dd.open !== null ? `below the ${dd.open.peak_session ?? "start"} high since ${dd.open.low_session}'s low` : undefined}
        />
        <Tile label="max drawdown (session)" value={fmtMoney(loss(s.historical))} tone={lossTone(s.historical)} n={s.sessions} nUnit="sessions" />
        <Tile label="reordered, 95th pct" value={fmtMoney(loss(s.reordered))} tone={lossTone(s.reordered)} title={stressTitle} />
        <Tile
          label={`blocks of ${String(s.block)}, 95th pct`}
          value={fmtMoney(loss(s.grouped))}
          tone={lossTone(s.grouped)}
          title={s.withheld !== null ? stressTitle : `${stressTitle}, moved in blocks of ${String(s.block)} so a bad stretch stays together`}
        />
      </TileGrid>
      {s.withheld !== null && (
        <p className="muted" style={{ fontSize: 11 }}>
          stress test withheld — {s.withheld}
        </p>
      )}

      {a.capital !== undefined && (
        <>
          <div className="card-subhead">capital</div>
          <CapitalTiles c={a.capital} />
        </>
      )}

      <div className="card-subhead">without the biggest winners</div>
      <DurabilityTable d={a.durability} />
      <p className="muted" style={{ fontSize: 11 }}>
        winners are removed from the result; the strategy is not re-run, so an arm whose entries depend on earlier ones
        would have traded differently without them
      </p>

      {dd.deepest.length > 0 && (
        <>
          <div className="card-subhead">deepest drawdowns</div>
          <EpisodesTable episodes={dd.deepest} />
          {dd.longest_closed !== null && (
            <p className="muted" style={{ fontSize: 11 }}>
              longest recovered: {dd.longest_closed.peak_session ?? "start"} → {dd.longest_closed.recovered_session},{" "}
              {String(dd.longest_closed.sessions_falling)} sessions falling and{" "}
              {String(dd.longest_closed.sessions_recovering ?? 0)} recovering
            </p>
          )}
        </>
      )}
      {(cov.entry_spot < cov.trades || cov.entry_time < cov.trades) && (
        <p className="muted" style={{ fontSize: 11 }}>
          recorded on {String(cov.entry_time)} of {String(cov.trades)} trades: entry time; {String(cov.entry_spot)}: entry spot;{" "}
          {String(cov.exit_spot)}: exit spot
        </p>
      )}
    </Card>
  );
}

export function AnalysisSlide({ module, mode = "paper" }: { module: PerformanceModuleId; mode?: TradingMode }) {
  const { data, isLoading, dataUpdatedAt } = useModuleAnalysis(module, "current", mode);

  if (isLoading || data === undefined) {
    return (
      <div className="cards cards-wide">
        <Card title="analysis">
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        </Card>
      </div>
    );
  }
  if (!data.ok) {
    return (
      <div className="cards cards-wide">
        <Card title="analysis" isError>
          <p className="muted">{data.error ?? "strategy analysis unavailable"}</p>
        </Card>
      </div>
    );
  }
  return (
    <div className="cards cards-wide">
      {data.mode === "live" && (
        <p className="muted" style={{ fontSize: 12 }}>
          the <strong>live</strong> ledger — a measurement of the live book
        </p>
      )}
      {data.era.note !== null && (
        <p className="muted" style={{ fontSize: 12 }}>
          scoped to the suite's current evidence window ({data.era.from ?? "all time"}) — {data.era.note}
          {data.era.moduleEra !== null && (
            <>
              {" "}· and to this module's own <strong>{data.era.moduleEra}</strong> era
            </>
          )}
        </p>
      )}
      {data.allocations.error !== null && (
        <p className="muted" style={{ fontSize: 12 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>
            allocations not applied
          </span>
          {data.allocations.error}
        </p>
      )}
      {data.groups.length === 0 ? (
        <Card title="analysis" updatedAt={dataUpdatedAt}>
          <p className="muted">no closed trades in this window yet</p>
        </Card>
      ) : (
        data.groups.map((g) => <ArmCard key={g.tag} module={module} tag={g.tag} a={g.analysis} updatedAt={dataUpdatedAt} />)
      )}
    </div>
  );
}
