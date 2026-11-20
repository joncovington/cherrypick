import { useState } from "react";
import type {
  BuilderRunCard,
  BuilderRunChange,
  BuilderRunResult,
  BuilderRunRobustness,
  BuilderRuns,
  BuilderRunTrade,
} from "@console/shared";
import { useBuilderRunCard } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * The Builder's explore runs (#1180): the list of an automation's Builder backtests, each with its net
 * curve and what it changed from the run before, and one run's whole card. Every figure is the run's
 * own (`backtest read automation-run[s]`), the robustness reads included: this draws, it computes
 * nothing. An explore run is description only: no trial, no verdict.
 */

const W = 1000;
const H = 220;
const DD_H = 60;
const PAD = 8;

function value(v: unknown): string {
  if (v === null || v === undefined) return "none";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** A trade's minute as a clock time: the driver records minutes of the day (630 is 10:30). */
export function clock(v: string | number | null): string {
  if (v === null) return "";
  if (typeof v === "string") return v;
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")}`;
}

/** A trade's legs in the suite's order chain, one per line: "STO 1 2026-12-18 585P". */
export function legChain(t: BuilderRunTrade): string {
  return t.legs
    .map((g) => `${Number(g.side) < 0 ? "STO" : "BTO"} ${t.quantity ?? 1} ${t.expiry ?? ""} ${g.strike ?? ""}${String(g.right ?? "").slice(0, 1).toUpperCase()}`)
    .join("\n");
}

/** "builder-<name>-YYMMDDHHMM" as "MM-DD HH:MM", the way the page names a run. */
export function runLabel(run: string): string {
  const m = /(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(run);
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : run;
}

/** One line for a run's changes: the first two keys and how many more. */
export function changeLine(changes: BuilderRunChange[], hasPrevious: boolean): string {
  if (!hasPrevious) return "first run";
  if (changes.length === 0) return "no change";
  const head = changes.slice(0, 2).map((c) => `${c.key} ${value(c.before)} → ${value(c.after)}`);
  return changes.length > 2 ? `${head.join("; ")} (+${changes.length - 2} more)` : head.join("; ");
}

/** An SVG path through `ys`, `x` across [x0, x1], scaled to [lo, hi] onto [top, bottom]. */
function line(ys: number[], lo: number, hi: number, x0: number, x1: number, top: number, bottom: number): string {
  if (ys.length === 0) return "";
  const span = hi - lo || 1;
  const step = ys.length > 1 ? (x1 - x0) / (ys.length - 1) : 0;
  return ys
    .map((y, i) => `${i ? "L" : "M"}${(x0 + i * step).toFixed(1)} ${(bottom - ((y - lo) / span) * (bottom - top)).toFixed(1)}`)
    .join(" ");
}

/** The card's chart: the net and gross lines over one scale, and the drawdown below its own zero. */
export function chartPaths(curve: BuilderRunCard["curve"]) {
  const nets = curve.map((p) => p.net);
  const grosses = curve.map((p) => p.gross);
  const lo = Math.min(0, ...nets, ...grosses);
  const hi = Math.max(0, ...nets, ...grosses);
  const zero = H - PAD - ((0 - lo) / (hi - lo || 1)) * (H - 2 * PAD);
  const worst = Math.min(0, ...curve.map((p) => p.dd));
  const ddTop = H + 12;
  // The drawdown hangs down from its own zero line: the worst point sits DD_H below it.
  const step = curve.length > 1 ? W / (curve.length - 1) : 0;
  const ddPoints = curve.map((p, i) => `L${(i * step).toFixed(1)} ${(ddTop + (worst ? p.dd / worst : 0) * DD_H).toFixed(1)}`);
  const dd = curve.length ? `M0 ${ddTop} ${ddPoints.join(" ")} L${W} ${ddTop} Z` : "";
  return {
    net: line(nets, lo, hi, 0, W, PAD, H - PAD),
    gross: line(grosses, lo, hi, 0, W, PAD, H - PAD),
    zero: zero.toFixed(1),
    dd,
    ddTop,
    worst,
  };
}

function Spark({ ys }: { ys: number[] }) {
  if (ys.length < 2) return <span className="muted">—</span>;
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const up = ys[ys.length - 1]! >= 0;
  const zero = 23 - ((0 - lo) / (hi - lo || 1)) * 20;
  return (
    <svg width="96" height="26" viewBox="0 0 96 26" role="img" aria-label={`cumulative net, ends ${up ? "above" : "below"} zero`}>
      <line x1="0" x2="96" y1={zero} y2={zero} className="builder-spark-zero" />
      <path d={line(ys, lo, hi, 0, 96, 3, 23)} className={up ? "builder-spark-up" : "builder-spark-down"} fill="none" />
    </svg>
  );
}

/** The explore-runs list: the queue's rows first, then each finished run, newest first. */
/** `reference` is the run pinned as the automation's backtest reference; `onPin` pins a run, or unpins with null. */
export function ExploreRunsList({
  data,
  onOpen,
  reference = null,
  onPin,
  pinning = false,
}: {
  data: BuilderRuns;
  onOpen: (run: string) => void;
  reference?: string | null;
  onPin?: (run: string | null) => void;
  pinning?: boolean;
}) {
  const live = data.tasks.filter((t) => t.status === "queued" || t.status === "running");
  const failed = data.tasks.filter((t) => t.status === "failed");
  return (
    <div className="builder-runs" data-runs={data.runs.length}>
      <div className="builder-runs-count">
        <span className="num">{data.runs.length}</span>
        <span className="muted">
          version{data.runs.length === 1 ? "" : "s"} tried on this data. Explore runs are not trials, so a study declared later
          should name how many versions were looked at first.
        </span>
      </div>
      {live.map((t) => (
        <p key={t.id ?? t.run} className="builder-run-live" data-status={t.status}>
          task {t.id ?? "?"} · {t.run} · {t.status}
        </p>
      ))}
      {failed.slice(0, 3).map((t) => (
        <p key={t.id ?? t.run} className="error">
          {runLabel(t.run)} failed{t.note ? `: ${t.note}` : ""}
        </p>
      ))}
      {data.runs.length > 0 && (
        <div className="table-scroll">
          <table className="builder-run-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Change</th>
                <th scope="col">Trades</th>
                <th scope="col">P&amp;L</th>
                <th scope="col">Curve</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((r: BuilderRunResult, i) => {
                const arm = Object.values(r.summary)[0];
                return (
                  <tr key={r.run}>
                    <td>
                      <button type="button" className="link-button" onClick={() => onOpen(r.run)}>
                        {runLabel(r.run)}
                      </button>
                      {onPin && (
                        <span className="builder-run-ref">
                          {r.run === reference ? (
                            <>
                              <span className="chip chip-ok">reference</span>
                              <button type="button" className="link-button" disabled={pinning} onClick={() => onPin(null)}>
                                Unpin
                              </button>
                            </>
                          ) : (
                            <button type="button" className="link-button" disabled={pinning} onClick={() => onPin(r.run)}>
                              Use as reference
                            </button>
                          )}
                        </span>
                      )}
                    </td>
                    <td className="builder-run-change">{changeLine(r.changes, i < data.runs.length - 1)}</td>
                    <td>{arm?.trades ?? "—"}</td>
                    <td>{fmtMoney(r.net)}</td>
                    <td>
                      <Spark ys={r.spark} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {onPin && data.runs.length > 0 && (
        <p className="muted">
          {reference === null
            ? "No run is this automation's backtest reference yet. Its page sets paper against the run you pin, and the yardstick moves only when you pin another."
            : `Paper is set against ${runLabel(reference)} on the automation's page.`}
        </p>
      )}
      {data.tasks.length === 0 && data.runs.length === 0 && <p className="muted">No backtest queued from here yet.</p>}
      {data.withheld > 0 && <p className="muted">{data.withheld} licensed result(s) not shown.</p>}
      {data.results_unavailable && <p className="error">The results could not be read.</p>}
    </div>
  );
}

function Tile({ label, value: v, foot }: { label: string; value: string; foot?: string }) {
  return (
    <div className="builder-tile">
      <span className="builder-tile-label">{label}</span>
      <span className="builder-tile-value num">{v}</span>
      {foot && <span className="builder-tile-foot">{foot}</span>}
    </div>
  );
}

/** "1.5×", the way a cost multiplier reads. */
function times(m: number): string {
  return `${m % 1 ? m.toFixed(1) : m.toFixed(0)}×`;
}

/** How the run holds up: what its best days carry, its costs scaled, its drawdown reshuffled, its quarters. */
export function RobustnessView({ r }: { r: BuilderRunRobustness }) {
  const best = r.best_sessions;
  const dd = r.drawdown;
  const q = r.quarters;
  return (
    <section className="builder-run-section builder-robustness">
      <div className="builder-run-section-head">
        <h4>How it holds up</h4>
        <span className="muted">in-sample reads of this one run, never a verdict</span>
      </div>
      <div className="builder-robustness-grid">
        <div className="builder-robustness-item" data-read="best-sessions">
          <h5>Best {best.k} days</h5>
          {"insufficient" in best ? (
            <p className="muted">Too few sessions ({best.n}) to set the best {best.k} apart.</p>
          ) : (
            <>
              <p className="num">
                {fmtMoney(best.best_net)} of {fmtMoney(best.total_net)}
                {best.share !== null ? ` · ${Math.round(best.share * 100)}%` : ""}
              </p>
              <p className={best.rests_on_best ? "error" : "muted"}>
                {best.rests_on_best
                  ? `Without them the other ${best.n - best.k} sessions average ${fmtMoney(best.rest_mean)}: the result is those days.`
                  : `The other ${best.n - best.k} sessions still average ${fmtMoney(best.rest_mean)} each.`}
              </p>
            </>
          )}
        </div>

        <div className="builder-robustness-item" data-read="cost-stress">
          <h5>Costs scaled</h5>
          {r.cost_stress === null ? (
            <p className="muted">No closed trades.</p>
          ) : (
            <>
              <table className="builder-run-table">
                <thead>
                  <tr>
                    <th scope="col">Fees + slippage</th>
                    <th scope="col">Per trade</th>
                  </tr>
                </thead>
                <tbody>
                  {r.cost_stress.rows.map((row) => (
                    <tr key={row.multiplier}>
                      <td>{times(row.multiplier)}</td>
                      <td>{fmtMoney(row.mean_net)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className={r.cost_stress.survives ? "muted" : "error"}>
                {r.cost_stress.survives
                  ? `Still pays at ${times(r.cost_stress.pass_stress)}, the bar a study must clear.`
                  : `Stops paying by ${times(r.cost_stress.pass_stress)}, the bar a study must clear.`}{" "}
                Settlement is not scaled.
              </p>
            </>
          )}
        </div>

        <div className="builder-robustness-item" data-read="drawdown">
          <h5>Drawdown reshuffled</h5>
          {dd.insufficient ? (
            <p className="muted">
              Actual {fmtMoney(-dd.actual)}. Too few trades to reshuffle ({dd.evidence_trades} of {dd.min_evidence ?? "?"}).
            </p>
          ) : (
            <>
              <p className="num">
                actual {fmtMoney(-dd.actual)} · p95 {fmtMoney(-(dd.drawdown_reduce ?? 0))} · p99 {fmtMoney(-(dd.drawdown_block ?? 0))}
              </p>
              <p className="muted">
                The same trades in {dd.resamples} reshuffled orders (week-long blocks), {dd.horizon} trades each. Longest losing
                streak p95 {dd.streak_reduce}, p99 {dd.streak_block}. Not a forecast or a floor.
              </p>
            </>
          )}
        </div>

        <div className="builder-robustness-item" data-read="quarters">
          <h5>Quarters</h5>
          <p className="num">
            {q.profitable} of {q.counted} profitable
          </p>
          <p className="muted">
            Counting quarters with at least {q.min_sessions} sessions traded
            {q.rows.length > q.counted ? `; ${q.rows.length - q.counted} thinner shown, not counted` : ""}.
          </p>
          <div className="builder-quarters">
            {q.rows.map((row) => (
              <span
                key={row.quarter}
                className={`builder-quarter ${row.net > 0 ? "up" : "down"}${row.counted ? "" : " thin"}`}
                title={`${row.quarter}: ${fmtMoney(row.net)}, ${row.trades} trades, ${row.sessions} sessions${row.counted ? "" : " (not counted)"}`}
              >
                {row.quarter.slice(2)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/** One run's whole card. */
export function RunCardView({ card }: { card: BuilderRunCard }) {
  const [gross, setGross] = useState(true);
  const [dd, setDd] = useState(true);
  const [log, setLog] = useState(false);
  const t = card.tiles;
  const p = chartPaths(card.curve);
  const maxSkip = Math.max(1, ...card.skips.map((s) => s.sessions));
  return (
    <div className="builder-run-card" data-run={card.run}>
      <div className="builder-run-head">
        <h3 className="num">
          {card.automation} · {runLabel(card.run)}
        </h3>
        <span className="chip chip-warn">explore · descriptive, not a verdict</span>
        {card.licensed && <span className="chip">licensed · shown while results may be</span>}
      </div>
      <p className="muted">
        {card.run} · source <span className="num">{card.source}</span>
        {card.sessions ? `, ${card.sessions[0]} → ${card.sessions[1]}` : ""} · {card.session_count ?? 0} sessions,{" "}
        {card.cadence ?? "stored cadence"} · paper's engine and fills, core fees
        {card.still_open ? ` · ${card.still_open} still open at the end` : ""}
      </p>
      <div className="builder-tiles">
        <Tile label="P&L" value={fmtMoney(t.net)} foot="net of fees, settlement, slippage" />
        {t.net_mid !== undefined && <Tile label="Net at mid" value={fmtMoney(t.net_mid)} foot="filled at mid: P&L before slippage" />}
        <Tile label="Trades" value={String(t.trades)} foot={t.sessions !== null ? `of ${t.sessions} sessions` : undefined} />
        <Tile label="Per trade" value={fmtMoney(t.per_trade)} foot="net" />
        <Tile label="Win rate" value={t.win_rate === null ? "—" : `${Math.round(t.win_rate * 100)}%`} />
        <Tile label="Max drawdown" value={fmtMoney(t.max_dd)} foot="closed trades" />
        <Tile label="Net / drawdown" value={t.net_over_dd === null ? "—" : t.net_over_dd.toFixed(2)} />
      </div>

      <section className="builder-run-section">
        <div className="builder-run-section-head">
          <h4>P&amp;L over time</h4>
          <button type="button" className="chip" aria-pressed={gross} onClick={() => setGross((v) => !v)}>
            gross line
          </button>
          <button type="button" className="chip" aria-pressed={dd} onClick={() => setDd((v) => !v)}>
            drawdown
          </button>
          <span className="muted">cumulative · net · by closed trade</span>
        </div>
        {card.curve.length === 0 ? (
          <p className="muted">No closed trades.</p>
        ) : (
          <svg viewBox={`0 0 ${W} ${H + DD_H + 16}`} width="100%" role="img" aria-label="Cumulative net P&L by closed trade, gross above it, drawdown beneath" className="builder-run-chart">
            <line x1="0" x2={W} y1={p.zero} y2={p.zero} className="builder-chart-zero" />
            {gross && <path d={p.gross} className="builder-chart-gross" fill="none" />}
            <path d={p.net} className="builder-chart-net" fill="none" />
            {dd && (
              <g>
                <line x1="0" x2={W} y1={p.ddTop} y2={p.ddTop} className="builder-chart-zero" />
                <path d={p.dd} className="builder-chart-dd" />
              </g>
            )}
          </svg>
        )}
        <p className="muted">The gap between gross and net is fees, settlement and slippage. An explore run has no split dates.</p>
      </section>

      <div className="builder-run-pair">
        <section className="builder-run-section">
          <div className="builder-run-section-head">
            <h4>By year</h4>
            <span className="muted">by exit session</span>
          </div>
          <div className="table-scroll">
            <table className="builder-run-table">
              <thead>
                <tr>
                  <th scope="col">Period</th>
                  <th scope="col">Trades</th>
                  <th scope="col">Gross</th>
                  <th scope="col">Fees</th>
                  <th scope="col">Settlement</th>
                  <th scope="col">Slippage</th>
                  <th scope="col">P&amp;L</th>
                  <th scope="col" title="gross - fees - settlement: the same trades filled at mid, before slippage">
                    Net at mid
                  </th>
                  <th scope="col">Max DD</th>
                </tr>
              </thead>
              <tbody>
                {card.years.map((y) => (
                  <tr key={y.period} className={y.period === "All" ? "builder-run-total" : undefined}>
                    <td>{y.period}</td>
                    <td>{y.trades}</td>
                    <td>{fmtMoney(y.gross)}</td>
                    <td>{fmtMoney(y.fees)}</td>
                    <td>{fmtMoney(y.settlement)}</td>
                    <td>{fmtMoney(y.slippage)}</td>
                    <td>{fmtMoney(y.net)}</td>
                    <td>{y.net_mid === undefined ? "—" : fmtMoney(y.net_mid)}</td>
                    <td>{fmtMoney(y.max_dd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">Gross − fees − settlement − slippage = P&amp;L on every row; the years add to All.</p>
        </section>

        <section className="builder-run-section">
          <div className="builder-run-section-head">
            <h4>Why sessions were skipped</h4>
            <span className="muted">the driver's own reasons</span>
          </div>
          {card.skips.length === 0 ? (
            <p className="muted">Every session entered.</p>
          ) : (
            <div className="builder-skips">
              {card.skips.map((s) => (
                <div key={s.reason} className="builder-skip">
                  <span>{s.reason.replaceAll("_", " ")}</span>
                  <span className="builder-skip-track">
                    <span className="builder-skip-fill" style={{ width: `${(s.sessions / maxSkip) * 100}%` }} />
                  </span>
                  <span className="num">{s.sessions}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {card.robustness && <RobustnessView r={card.robustness} />}

      <section className="builder-run-section">
        <div className="builder-run-section-head">
          <h4>What this run changed</h4>
          <span className="muted">{card.previous ? `${runLabel(card.previous)} → ${runLabel(card.run)}` : "first run"}</span>
        </div>
        {card.previous === null ? (
          <p className="muted">The first run of this automation: nothing to compare with.</p>
        ) : card.changes.length === 0 ? (
          <p className="muted">The same automation as the run before.</p>
        ) : (
          <ul className="builder-run-changes num">
            {card.changes.map((c) => (
              <li key={c.key}>
                {c.key}: {value(c.before)} → {value(c.after)}
              </li>
            ))}
          </ul>
        )}
        <div className="builder-editor-actions">
          <button type="button" className="btn" onClick={() => setLog((v) => !v)}>
            {log ? "Hide trade log" : "Trade log"}
          </button>
          <button type="button" className="btn" disabled title="A study declaration is written and reviewed outside the page">
            Declare this spec…
          </button>
        </div>
        <p className="muted">
          The change is read from the automation each run stored. Declaring is a study declaration made outside the page, before any
          holdout is read.
        </p>
        {log && (
          <div className="table-scroll">
            <table className="builder-run-table builder-trade-log">
              <thead>
                <tr>
                  <th scope="col">Entered</th>
                  <th scope="col">Closed</th>
                  <th scope="col">Exit</th>
                  <th scope="col">Legs</th>
                  <th scope="col">Gross</th>
                  <th scope="col">Fees</th>
                  <th scope="col">Settlement</th>
                  <th scope="col">Slippage</th>
                  <th scope="col">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {card.trades.map((x, i) => (
                  <tr key={i}>
                    <td>
                      {x.session} {clock(x.entry_minute)}
                    </td>
                    <td>
                      {x.exit_session} {clock(x.exit_minute)}
                    </td>
                    <td>{x.exit_kind ?? "—"}</td>
                    <td className="builder-legs">{legChain(x)}</td>
                    <td>{fmtMoney(x.gross)}</td>
                    <td>{fmtMoney(x.fees)}</td>
                    <td>{fmtMoney(x.settlement)}</td>
                    <td>{fmtMoney(x.slippage)}</td>
                    <td>{fmtMoney(x.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

/** One run's card, read when opened. */
export function RunCardPanel({ name, run, onClose }: { name: string; run: string; onClose: () => void }) {
  const q = useBuilderRunCard(name, run);
  return (
    <div className="builder-run-panel">
      <button type="button" className="btn" onClick={onClose}>
        ← back to the runs
      </button>
      {q.isLoading && <div className="muted">Reading the run…</div>}
      {q.error && <div className="error">Could not read the run: {String((q.error as Error).message ?? q.error)}</div>}
      {q.data && <RunCardView card={q.data} />}
    </div>
  );
}
