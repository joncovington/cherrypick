import type {
  BacktestArmReport,
  BacktestGapStats,
  BacktestInventory,
  BacktestInventoryStore,
  BacktestOverview,
  BacktestRunReport,
  BacktestStore,
  BacktestSideReading,
  BacktestValidation,
  BridgedResult,
  StudyResult,
  StudyRow,
} from "@console/shared";
import { isBacktestCapital } from "@console/shared";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { Card } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtGap, signTone, useBridged } from "../../lib/bridged";
import { fmtCash, fmtCount, fmtNum } from "../../lib/format";
import { RunCards } from "./RunCards";
import { indexStudies, studyHref, useStudies, type StudyIndex } from "./studyIndex";

/**
 * The Backtest page (2026-10-17): `packages/backtest`'s replays over licensed vendor history, read
 * through `/api/backtest/overview` and `/api/backtest/runs/:id` (`services/backtestBridge.ts`).
 *
 * The page is reached only while the suite says `backtest: true` (`visibility.ts`, fail closed), and
 * the server refuses both reads on its own otherwise. Every verdict here is the module's: the
 * unlock, each check, the paired interval, `no_exposure`. Nothing is recomputed; a null is an em
 * dash, and tone comes only from a net figure's sign (interval ends are never toned).
 *
 * Money is the suite's one layout: "net" is net of fees, signed, whole-position dollars. A run's
 * per-session figures are the replay ledger's `net_pnl`; its fees are already out of them and are
 * shown beside, never subtracted again.
 */

function LicenceNote() {
  return (
    <div className="integrity-banner" role="note">
      <strong>Licensed vendor data.</strong> Everything on this page is computed from historical data
      licensed to this machine&apos;s owner. It stays on this console: do not screenshot, export, copy or
      share it.
    </div>
  );
}

function useOverview() {
  return useBridged<BacktestOverview>(["backtest", "overview"], "/api/backtest/overview", 60_000);
}

/** The shared head of every tab: the note, then the overview's loading/failure state. */
function WithOverview({ children }: { children: (d: BacktestOverview, updatedAt: number) => ReactNode }) {
  const { data, isLoading, isError, error, dataUpdatedAt } = useOverview();
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "backtest read unavailable"}</p>;
  else body = children(data.data, dataUpdatedAt);
  return (
    <>
      <LicenceNote />
      {body}
    </>
  );
}

// ------------------------------------------------------------------ formatting

function span(s: [string, string] | null | undefined): string {
  return s === null || s === undefined ? "—" : `${s[0]} → ${s[1]}`;
}

/** A fraction 0-1 as a percentage: `98.7%`. */
function frac(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

function gapCell(g: BacktestGapStats | null | undefined): string {
  if (g === null || g === undefined) return "—";
  return `${fmtGap(g.median ?? null)} / ${fmtGap(g.p90 ?? null)} / ${fmtGap(g.p99 ?? null)}`;
}

/** An open-shaped measure value, as written. */
function measure(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? fmtCount(v) : String(Math.round(v * 10_000) / 10_000);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function pass(v: boolean | undefined | null): ReactNode {
  if (v === undefined || v === null) return <span className="muted">—</span>;
  return <span className={v ? "chip chip-ok" : "chip chip-missing"}>{v ? "pass" : "fail"}</span>;
}

/** `created_at` (epoch seconds) as New York wall clock, `2026-10-17 09:30`. */
function etStamp(sec: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(sec * 1000));
  const p = (t: string) => parts.find((x) => x.type === t)?.value ?? "";
  return `${p("year")}-${p("month")}-${p("day")} ${p("hour")}:${p("minute")}`;
}

function Tile({ label, value, title, cls }: { label: string; value: ReactNode; title?: string; cls?: string }) {
  return (
    <div className="stat-tile" title={title}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

// ------------------------------------------------------------------ data

export function BacktestDataTab() {
  return (
    <WithOverview>
      {(d, updatedAt) => {
        const l = d.license;
        return (
          <div className="cards cards-wide">
            <TrustCard validation={d.validation} updatedAt={updatedAt} />
            <Card title="licence" updatedAt={updatedAt}>
              {l.active && l.terminated_on === null ? (
                <p style={{ margin: 0 }}>
                  Active, with no end date set. When it ends, every vendor-derived file must be deleted by its purge date.
                </p>
              ) : (
                <TileGrid count={4}>
                  <Tile label="licence" value={l.active ? "active" : "terminated"} cls={l.active ? "" : "pnl-neg"} />
                  <Tile label="terminated on" value={l.terminated_on ?? "—"} />
                  <Tile
                    label="purge by"
                    value={l.purge_by ?? "—"}
                    title="the date every vendor-derived file must be deleted by, once the licence ends"
                  />
                  <Tile
                    label="days left"
                    value={l.overdue ? <span className="chip chip-missing">overdue</span> : l.days_left === null ? "—" : String(l.days_left)}
                    title="days until the purge date"
                  />
                </TileGrid>
              )}
              {l.overdue && (
                <p className="muted" style={{ fontSize: 12 }}>
                  The purge date has passed: <code>python -m cherrypick.backtest purge</code> lists what must go.
                </p>
              )}
            </Card>
            {d.inventory ? (
              <StoreInventory inv={d.inventory} stores={d.stores} updatedAt={updatedAt} />
            ) : (
              <VendorStores d={d} updatedAt={updatedAt} />
            )}
          </div>
        );
      }}
    </WithOverview>
  );
}

/**
 * Is history trusted? The module's unlock verdict per validation, first, with what stands in the way:
 * the checks outside tolerance, or (none outside) the sessions compared so far. The comparison
 * tables are behind an expand. Folded in from its own tab 2026-10-30.
 */
export function TrustCard({ validation, updatedAt }: { validation: BacktestValidation[]; updatedAt: number }) {
  const unlocked = validation.filter((v) => v.unlocks_history);
  const answer =
    validation.length === 0
      ? "Not yet asked: no validation has run."
      : unlocked.length > 0
        ? `Yes: ${unlocked.length} of ${validation.length} validation${validation.length === 1 ? "" : "s"} unlock${unlocked.length === 1 ? "s" : ""} history.`
        : `No: ${validation.length === 1 ? "the one validation does not unlock" : `none of the ${validation.length} validations unlocks`} history, so every replay over it is provisional, not evidence.`;
  return (
    <Card title="is history trusted?" updatedAt={updatedAt}>
      <p style={{ margin: "0 0 6px" }}>
        <strong>{answer}</strong>
      </p>
      {validation.length === 0 ? (
        <p className="muted">
          <code>python -m cherrypick.backtest validate</code> compares the vendor store against the recorder&apos;s own sessions.
        </p>
      ) : (
        <>
          <ul style={{ margin: "0 0 6px", paddingLeft: "1.1em" }}>
            {validation.map((v) => {
              const failing = Object.entries(v.vendor_within_tolerance ?? {})
                .filter(([, ok]) => ok === false)
                .map(([n]) => n)
                .sort();
              return (
                <li key={v.file}>
                  <span className={v.unlocks_history ? "chip chip-ok" : "chip chip-warn"}>
                    {v.unlocks_history ? "unlocks" : "does not unlock"}
                  </span>{" "}
                  {v.cadence === null ? "unknown cadence" : `${String(v.cadence)} s`}, {v.sessions_compared ?? "—"} session(s) compared
                  {v.dates.length > 0 ? ` (${v.dates[0]!} to ${v.dates[v.dates.length - 1]!})` : ""}.{" "}
                  {v.unlocks_history
                    ? null
                    : failing.length > 0
                      ? `Outside tolerance: ${failing.join(", ")}.`
                      : "Every check is within tolerance; it needs more sessions compared."}
                </li>
              );
            })}
          </ul>
          <details>
            <summary className="muted">the comparison tables</summary>
            <div className="cards cards-wide" style={{ marginTop: 8 }}>
              {validation.map((v) => (
                <ValidationCard key={v.file} v={v} updatedAt={updatedAt} />
              ))}
            </div>
          </details>
        </>
      )}
    </Card>
  );
}

/** The replay stores' fetch record, shown only when the inventory could not be read. */
function VendorStores({ d, updatedAt }: { d: BacktestOverview; updatedAt: number }) {
  return (
            <Card title="vendor stores" updatedAt={updatedAt}>
              {d.stores.length === 0 ? (
                <p className="muted">No vendor store on this machine yet.</p>
              ) : (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>symbol</th>
                        <th>cadence</th>
                        <th>file</th>
                        <th className="num" title="sessions stored complete">sessions</th>
                        <th>span</th>
                        <th className="num" title="sessions the fetch refused to store">refused</th>
                        <th>why refused</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.stores.map((s) => (
                        <tr key={s.file}>
                          <td>{s.symbol}</td>
                          <td>{`${String(s.cadence)} s`}</td>
                          <td className="muted">{s.file}</td>
                          <td className="num">{fmtCount(s.complete)}</td>
                          <td>{span(s.span)}</td>
                          <td className="num">{fmtCount(s.refused)}</td>
                          <td className="muted" style={{ fontSize: 12 }}>
                            {Object.entries(s.refused_reasons)
                              .map(([k, n]) => `${k} ${String(n)}`)
                              .join(", ") || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
  );
}

/** "2.65 GB" / "31 MB". */
export function fmtBytes(b: number | undefined): string {
  if (b === undefined) return "—";
  return b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${Math.round(b / 1e6)} MB`;
}

/** The largest table's rows and span, the one line a store is read by. */
export function mainTable(s: BacktestInventoryStore): string {
  const t = Object.entries(s.tables ?? {}).sort(([, a], [, b]) => b.rows - a.rows)[0];
  if (!t) return "—";
  const [name, v] = t;
  return `${name}: ${v.rows.toLocaleString("en-US")} rows${v.span ? ` · ${v.span[0]} to ${v.span[1]}` : ""}`;
}

/** "412 · half_day 2": a replay store's sessions the fetch refused, and why. */
function refusedCell(s: BacktestStore | undefined): string {
  if (!s || s.refused === 0) return "—";
  const why = Object.entries(s.refused_reasons)
    .map(([k, n]) => `${k} ${String(n)}`)
    .join(", ");
  return why ? `${fmtCount(s.refused)} · ${why}` : fmtCount(s.refused);
}

function StoreInventory({ inv, stores, updatedAt }: { inv: BacktestInventory; stores: BacktestStore[]; updatedAt: number }) {
  // The replay stores' fetch record, joined by file: one table, not two (2026-10-30).
  const fetched = new Map(stores.map((s) => [s.file, s]));
  return (
    <Card title="every store, and the code that rebuilds it" updatedAt={updatedAt}>
      <p className="muted" style={{ fontSize: 12 }}>
        The vendor data is licensed: the backup skips it and <code>purge</code> removes it when the licence ends. What keeps a
        store usable is that it can be rebuilt, so each one names its fetch code (<code>packages/backtest/stores.py</code>).
      </p>
      {inv.error ? <p className="pnl-neg">{inv.error}</p> : null}
      {inv.undeclared.length > 0 ? (
        <p className="pnl-neg">
          Undeclared: {inv.undeclared.map((u) => u.file).join(", ")}. Add each to <code>stores.py</code> with the code that
          builds it.
        </p>
      ) : null}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th title="The file, and the code that rebuilds it">store</th>
              <th>holds</th>
              <th>largest table</th>
              <th className="num">size</th>
              <th title="Sessions the fetch refused to store, and why">refused</th>
              <th>used by</th>
            </tr>
          </thead>
          <tbody>
            {inv.stores.map((s) => (
              <tr key={s.file}>
                <td>
                  {s.file}
                  {s.present === false ? <span className="chip chip-warn"> missing</span> : null}
                  <div style={{ fontSize: 12 }}>
                    <code>{s.built_by}</code>
                  </div>
                </td>
                <td className="muted" style={{ fontSize: 12 }}>{s.holds}</td>
                <td className="muted" style={{ fontSize: 12 }}>{s.present ? mainTable(s) : "—"}</td>
                <td className="num">{fmtBytes(s.bytes)}</td>
                <td className="muted" style={{ fontSize: 12 }}>{refusedCell(fetched.get(s.file))}</td>
                <td className="muted" style={{ fontSize: 12 }}>{s.used_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ validation

function checkNames(v: BacktestValidation): string[] {
  const names = new Set<string>([
    ...Object.keys(v.vendor_at_least_recorder),
    ...Object.keys(v.recorder.checks ?? {}),
    ...Object.keys(v.vendor.checks ?? {}),
  ]);
  return [...names].sort();
}

function MeasuresTable({ recorder, vendor }: { recorder: BacktestSideReading; vendor: BacktestSideReading }) {
  const keys = [...new Set([...Object.keys(recorder.measures ?? {}), ...Object.keys(vendor.measures ?? {})])].sort();
  if (keys.length === 0) return <p className="muted">No measures recorded.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>measure</th>
            <th>recorder</th>
            <th>vendor</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{measure(recorder.measures?.[k])}</td>
              <td>{measure(vendor.measures?.[k])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ValidationCard({ v, updatedAt }: { v: BacktestValidation; updatedAt: number }) {
  const sessions = Object.entries(v.sessions).sort(([a], [b]) => a.localeCompare(b));
  const names = checkNames(v);
  return (
    <Card title={`vendor vs recorder — ${v.cadence === null ? "unknown cadence" : `${String(v.cadence)} s`}`} updatedAt={updatedAt}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap", margin: "0.2rem 0 0.6rem" }}>
        <span
          className={v.unlocks_history ? "chip chip-ok" : "chip chip-warn"}
          style={{ fontSize: 14, padding: "3px 10px" }}
          title="the module's verdict: enough sessions compared, and the vendor replay passes every check the recorder replay passes"
        >
          {v.unlocks_history ? "unlocks history" : "does not unlock history"}
        </span>
        <span className="muted" style={{ fontSize: 12 }}>
          {v.sessions_compared === null ? "—" : String(v.sessions_compared)} session(s) compared · {v.file}
          {v.dates.length > 0 && ` · ${v.dates[0]!} → ${v.dates[v.dates.length - 1]!}`}
        </span>
      </div>

      <div className="card-subhead">decision checks</div>
      {names.length === 0 ? (
        <p className="muted">No checks recorded.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>check</th>
                <th>recorder</th>
                <th>vendor</th>
                <th title="the bar since 2026-10-25: each check's measure within the declared tolerance of the recording's at the vendor's cadence">within tolerance</th>
                <th title="the old pass/fail comparison, kept for the record: not the bar">pass ≥ pass</th>
              </tr>
            </thead>
            <tbody>
              {names.map((n) => (
                <tr key={n}>
                  <td>{n}</td>
                  <td>{pass(v.recorder.checks?.[n])}</td>
                  <td>{pass(v.vendor.checks?.[n])}</td>
                  <td>{v.vendor_within_tolerance && n in v.vendor_within_tolerance ? pass(v.vendor_within_tolerance[n]) : "—"}</td>
                  <td className="muted">{pass(v.vendor_at_least_recorder[n])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-subhead">replay measures</div>
      <MeasuresTable recorder={v.recorder} vendor={v.vendor} />

      <div className="card-subhead">quotes, per session</div>
      {sessions.length === 0 ? (
        <p className="muted">No sessions compared.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>session</th>
                <th>layer</th>
                <th title="recorded quotes the vendor store matched in time">matched</th>
                <th title="of those matched, identical bid and ask">identical</th>
                <th title="recorder mid − vendor mid, per share: median / p90 / p99">mid gap</th>
                <th title="mean of recorder mid − vendor mid, per share">mid mean</th>
                <th title="recorder spot − vendor spot, index points: median / p90 / p99">spot gap</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(([day, s]) => {
                const q = s.quotes;
                if (q === undefined || !q.ok) {
                  return (
                    <tr key={day}>
                      <td>{day}</td>
                      <td>{q === undefined ? <span className="muted">—</span> : pass(false)}</td>
                      <td colSpan={5} className="muted">{q?.reason ?? "not compared"}</td>
                    </tr>
                  );
                }
                return (
                  <tr key={day}>
                    <td>{day}</td>
                    <td>{pass(true)}</td>
                    <td>{frac(q.matched)}</td>
                    <td>{frac(q.identical)}</td>
                    <td>{gapCell(q.mid_gap)}</td>
                    <td>{fmtGap(q.mid_gap?.mean_signed ?? null, 3)}</td>
                    <td>{gapCell(q.spot_gap)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-subhead">GEX profile, per session</div>
      {sessions.length === 0 ? (
        <p className="muted">No sessions compared.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>session</th>
                <th>layer</th>
                <th title="recorder profiles paired with a vendor profile">pairs</th>
                <th title="pairs with the same call wall">call wall</th>
                <th title="pairs with the same put wall">put wall</th>
                <th title="pairs with the same net GEX sign">net sign</th>
                <th title="recorder gamma flip − vendor gamma flip, index points: median / p90 / p99">flip gap</th>
                <th title="median strikes per profile: recorder / vendor">strikes</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(([day, s]) => {
                const p = s.profile;
                if (p === undefined || (!p.ok && (p.pairs ?? 0) === 0)) {
                  return (
                    <tr key={day}>
                      <td>{day}</td>
                      <td>{p === undefined ? <span className="muted">—</span> : pass(false)}</td>
                      <td colSpan={6} className="muted">{p?.reason ?? (p === undefined ? "not compared" : "no profiles paired")}</td>
                    </tr>
                  );
                }
                return (
                  <tr key={day}>
                    <td>{day}</td>
                    <td>{pass(p.ok)}</td>
                    <td>{fmtCount(p.pairs ?? null)}</td>
                    <td>{frac(p.call_wall_same)}</td>
                    <td>{frac(p.put_wall_same)}</td>
                    <td>{frac(p.net_gex_sign_same)}</td>
                    <td>{gapCell(p.gamma_flip_gap)}</td>
                    <td>
                      {measure(p.strikes_per_profile?.recorder_median)} / {measure(p.strikes_per_profile?.vendor_median)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}


// ------------------------------------------------------------------ runs

function useRun(id: string | null, capital: number | null) {
  return useQuery<BridgedResult<BacktestRunReport>>({
    // The default account (the run's peak risk) keeps the plain key; a chosen one is its own entry.
    queryKey: capital === null ? ["bridged", "backtest", "run", id] : ["bridged", "backtest", "run", id, capital],
    enabled: id !== null,
    // Keep the last account's cards on screen while another loads.
    placeholderData: (prev) => prev,
    queryFn: async () => {
      const q = capital === null ? "" : `?capital=${String(capital)}`;
      const res = await fetch(`/api/backtest/runs/${encodeURIComponent(id ?? "")}${q}`);
      if (!res.ok) throw new Error(`run ${id ?? ""}: HTTP ${String(res.status)}`);
      return (await res.json()) as BridgedResult<BacktestRunReport>;
    },
    // A run's report is written once and never changes.
    staleTime: Infinity,
  });
}

function ci(v: [number, number] | null | undefined): string {
  return v === null || v === undefined ? "—" : `${fmtCash(v[0])} to ${fmtCash(v[1])}`;
}

function ArmsTable({ arms }: { arms: Array<[string, BacktestArmReport]> }) {
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>arm</th>
            <th>sessions</th>
            <th>trades</th>
            <th title="trading fees over the run, whole-position dollars; already out of every net figure, never subtracted again">fees</th>
            <th title="the sum of per-session net (net of fees), whole-position dollars">net</th>
            <th title="mean per-session net">net / session</th>
            <th title="standard deviation of per-session net">sd</th>
            <th title="block-bootstrap 95% interval on the mean per-session net">mean 95% CI</th>
            <th title="mean / sd per session">sharpe</th>
            <th title="probabilistic Sharpe ratio against zero">PSR</th>
            <th title="deflated Sharpe ratio at the trial count">DSR</th>
            <th title="trials the DSR is deflated by">trials</th>
            <th title="sessions the record would need for its Sharpe to be distinguishable from zero">min record</th>
          </tr>
        </thead>
        <tbody>
          {arms.map(([arm, a]) => {
            const s = a.summary;
            return (
              <tr key={arm}>
                <td>
                  {arm}
                  {a.no_exposure === true && (
                    <span
                      className="chip chip-warn"
                      style={{ marginLeft: 6 }}
                      title="every session matched control's to the cent, trade count included: this arm measured nothing"
                    >
                      no exposure
                    </span>
                  )}
                </td>
                <td>{fmtCount(s.sessions)}</td>
                <td>{fmtCount(a.trades ?? null)}</td>
                <td>{a.fees === null || a.fees === undefined ? "—" : fmtCash(-Math.abs(a.fees))}</td>
                <td className={signTone(s.total)}>{fmtCash(s.total)}</td>
                <td className={signTone(s.mean)}>{fmtCash(s.mean)}</td>
                <td>{fmtNum(s.sd)}</td>
                <td>{ci(s.mean_ci95_block_bootstrap)}</td>
                <td>{fmtNum(s.sharpe_per_session, 3)}</td>
                <td>{fmtNum(s.psr, 3)}</td>
                <td>{fmtNum(s.dsr, 3)}</td>
                <td>{fmtCount(s.trials)}</td>
                <td>{fmtNum(s.min_track_record, 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PairingTable({ arms }: { arms: Array<[string, BacktestArmReport]> }) {
  const paired = arms.filter(([arm]) => arm !== "control");
  if (paired.length === 0) return <p className="muted">Only control ran: nothing to pair.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>arm − control</th>
            <th title="sessions both arms traded">sessions</th>
            <th title="sum of per-session net differences, whole-position dollars">net diff</th>
            <th title="mean per-session net difference">diff / session</th>
            <th title="standard deviation of the per-session difference">sd</th>
            <th title="interval on the mean difference">CI</th>
            <th title="one-sided t lower bound on the mean difference">t lower</th>
            <th title="sessions the arm beat control / control beat the arm / ties">sign</th>
            <th title="two-sided sign-test p">sign p</th>
            <th title="sessions only the arm / only control traded">one-sided</th>
          </tr>
        </thead>
        <tbody>
          {paired.map(([arm, a]) => {
            const v = a.vs_control;
            if (v === null || v === undefined) {
              return (
                <tr key={arm}>
                  <td>{arm}</td>
                  <td colSpan={9} className="muted">
                    not paired: a side has no dated sessions
                  </td>
                </tr>
              );
            }
            return (
              <tr key={arm}>
                <td>
                  {arm}
                  {a.no_exposure === true && (
                    <span className="chip chip-warn" style={{ marginLeft: 6 }}>
                      no exposure
                    </span>
                  )}
                </td>
                <td>{fmtCount(v.sessions)}</td>
                <td className={signTone(v.total_diff)}>{fmtCash(v.total_diff)}</td>
                <td className={signTone(v.mean_diff)}>{fmtCash(v.mean_diff)}</td>
                <td>{fmtNum(v.sd_diff)}</td>
                <td title={v.ci_level === undefined ? undefined : `${String(Math.round(v.ci_level * 100))}% interval`}>{ci(v.ci)}</td>
                <td>{v.t_lower === undefined ? "—" : fmtCash(v.t_lower)}</td>
                <td>
                  {v.arm_better === undefined ? "—" : `${String(v.arm_better)} / ${String(v.control_better ?? 0)} / ${String(v.ties ?? 0)}`}
                </td>
                <td>{fmtNum(v.sign_test_p ?? null, 3)}</td>
                <td>
                  {v.arm_only_sessions === undefined ? "—" : `${String(v.arm_only_sessions)} / ${String(v.control_only_sessions ?? 0)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The cards' account from the URL: whole dollars, or null for the run's peak risk. */
function capitalParam(raw: string | null): number | null {
  return raw !== null && isBacktestCapital(raw) ? Number(raw) : null;
}

function RunReport({ id, study }: { id: string; study?: { row: StudyRow; result: StudyResult } }) {
  const [params, setParams] = useSearchParams();
  const capital = capitalParam(params.get("capital"));
  const pickCapital = (c: number | null) => {
    const next = new URLSearchParams(params);
    if (c === null) next.delete("capital");
    else next.set("capital", String(c));
    setParams(next, { replace: true });
  };
  const { data, isLoading, isError, error, dataUpdatedAt } = useRun(id, capital);
  const r = data?.data ?? null;
  return (
    <Card title={`run ${id}`} updatedAt={dataUpdatedAt} isError={isError || (data !== undefined && !data.ok)}>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : isError ? (
        <p className="muted">{(error as Error).message}</p>
      ) : data === undefined || !data.ok || r === null ? (
        <p className="muted">{data?.error ?? "run unavailable"}</p>
      ) : (
        <>
          <RunPlainEnglish r={r} study={study} />
          {r.provisional === true && (
            <p className="muted">
              <ProvisionalChip /> {r.provisional_note ?? "Provisional: not a trial and not evidence."}
            </p>
          )}
          <TileGrid count={6}>
            <Tile label="window" value={r.window} />
            <Tile label="span" value={span(r.span)} />
            <Tile label="cadence" value={`${String(r.cadence)} s`} />
            <Tile label="fill model" value={measure(r.fill_model)} />
            <Tile
              label="trials (module)"
              value={r.trials_for_module === null ? "—" : String(r.trials_for_module)}
              title="design-window trials recorded for this module; the DSR deflates by it"
            />
            <Tile label="refused sessions" value={String(Object.keys(r.refused).length)} />
          </TileGrid>
          <p className="muted" style={{ fontSize: 12 }}>
            code <code>{r.code_commit ?? "—"}</code> · data fingerprint <code>{r.data_fingerprint ?? "—"}</code>
          </p>
          <div className="card-subhead">arms</div>
          <ArmsTable arms={Object.entries(r.arms)} />
          <div className="card-subhead">paired with control, by session</div>
          <PairingTable arms={Object.entries(r.arms)} />
          <div className="card-subhead">run cards</div>
          <RunCards cards={r.cards} capital={capital} onCapital={pickCapital} />
          <div className="card-subhead">refused sessions</div>
          {Object.keys(r.refused).length === 0 ? (
            <p className="muted">None.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>session</th>
                    <th>why</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(r.refused)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([day, why]) => (
                      <tr key={day}>
                        <td>{day}</td>
                        <td className="muted">{why}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/** "item 23 · design": the study a run belongs to, from the result file that names it. */
function StudyCell({ id, index }: { id: string; index: StudyIndex }) {
  const hit = index.byRun.get(id);
  if (!hit) return <span className="muted">—</span>;
  const where = hit.result.window ?? hit.result.name;
  return (
    <Link to={studyHref(hit.row.study)} title={hit.row.title}>
      item {hit.row.id} · {where}
    </Link>
  );
}

/** Which way an interval sits against zero, in words. */
function against(ci: [number, number] | null | undefined): string {
  if (!ci) return "";
  if (ci[0] > 0) return "the interval is above zero";
  if (ci[1] < 0) return "the interval is below zero";
  return "the interval includes zero";
}

/**
 * The run in plain words, before the tables: what ran, which study it answers and that study's
 * verdict, whether it counts as evidence, and each arm against control. Every figure is the
 * report's; the card only words it.
 */
export function RunPlainEnglish({ r, study }: { r: BacktestRunReport; study?: { row: StudyRow; result: StudyResult } }) {
  const arms = Object.entries(r.arms);
  const control = r.arms["control"];
  return (
    <div className="plain-english">
      <div className="card-subhead">in plain English</div>
      <p>
        A replay of the {r.window} window, {span(r.span)}, at {String(r.cadence)} s ticks, with {arms.length} arm
        {arms.length === 1 ? "" : "s"}.{" "}
        {study ? (
          <>
            It is <Link to={studyHref(study.row.study)}>item {study.row.id}</Link>&apos;s {study.result.window ?? "result"} run: &ldquo;
            {study.row.title}&rdquo;. The study&apos;s verdict here is <strong>{study.result.summary ?? "not written"}</strong>
            {study.row.outcome ? `, and the decision: ${study.row.outcome}` : ""}.
          </>
        ) : (
          "No study in the register names this run."
        )}
      </p>
      {control && (
        <p>
          Control made {fmtCash(control.summary.mean)} a session on average, {fmtCash(control.summary.total)} over the run.
        </p>
      )}
      <ul style={{ margin: "0 0 8px", paddingLeft: "1.1em" }}>
        {arms
          .filter(([arm]) => arm !== "control")
          .map(([arm, a]) => {
            const v = a.vs_control;
            if (a.no_exposure === true) return <li key={arm}>{arm} measured nothing: every session matched control&apos;s to the cent.</li>;
            if (!v || v.mean_diff === null) return <li key={arm}>{arm}: not paired with control.</li>;
            return (
              <li key={arm}>
                {arm}: {fmtCash(v.mean_diff)} a session against control; {against(v.ci)} ({ci(v.ci)}).
              </li>
            );
          })}
      </ul>
    </div>
  );
}

/**
 * The trials the DSR deflates by, and the provisional looks that are not trials. With looks and no
 * trials, a provisional run's DSR is deflated by nothing, so it reads no stricter than its PSR; the
 * line says so rather than let "trials 1" pass for one look.
 */
export function trialsLine(d: BacktestOverview): string {
  const list = (r: Record<string, number> | undefined) =>
    Object.entries(r ?? {})
      .map(([m, n]) => `${m} ${String(n)}`)
      .join(", ");
  const trials = list(d.trials);
  const looks = list(d.provisional_looks);
  const head = `Trials recorded: ${trials || "none"}.`;
  if (!looks) return head;
  return (
    `${head} Provisional looks, distinct specs run before validation unlocked: ${looks}. ` +
    `A look is not a trial, so ${trials ? "a provisional run's DSR is deflated by the trials alone" : "a provisional run's DSR is deflated by nothing and reads no stricter than its PSR"}.`
  );
}

export function BacktestRunsTab() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("run");
  const studies = useStudies();
  const index = indexStudies(studies.data?.ok ? studies.data.data : null);
  const pick = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("run", id);
    setParams(next, { replace: true });
  };
  return (
    <WithOverview>
      {(d, updatedAt) => (
        <div className="cards cards-wide">
          <Card title="runs" updatedAt={updatedAt}>
            <p className="muted" style={{ fontSize: 12 }}>
              {trialsLine(d)} Select a run to read its report.
            </p>
            {d.runs.length === 0 ? (
              <p className="muted">No runs yet.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>run</th>
                      <th title="The study whose result names this run">study</th>
                      <th>created (ET)</th>
                      <th>module</th>
                      <th>window</th>
                      <th>arms</th>
                      <th>span</th>
                      <th className="num">cadence</th>
                      <th className="num">sessions</th>
                      <th className="num">refused</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.runs.map((r) => (
                      <tr key={r.run_id} className={r.run_id === selected ? "row-selected" : undefined}>
                        <td>
                          <button
                            type="button"
                            className="btn btn-quiet"
                            onClick={() => pick(r.run_id)}
                            aria-pressed={r.run_id === selected}
                          >
                            {r.run_id}
                          </button>
                        </td>
                        <td>
                          <StudyCell id={r.run_id} index={index} />
                        </td>
                        <td className="muted">{etStamp(r.created_at)}</td>
                        <td>{r.module}</td>
                        <td>
                          {r.window}
                          {r.provisional === true && <ProvisionalChip />}
                        </td>
                        <td>{r.arms.join(", ")}</td>
                        <td>{span(r.span)}</td>
                        <td className="num">{`${String(r.cadence)} s`}</td>
                        <td className="num">{fmtCount(r.sessions)}</td>
                        <td className="num">{fmtCount(r.refused)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          {selected !== null && <RunReport id={selected} study={index.byRun.get(selected)} />}
        </div>
      )}
    </WithOverview>
  );
}

/** A run made before validation unlocked history: shown as such wherever it appears. */
function ProvisionalChip() {
  return (
    <Link
      to="/backtest/data"
      className="chip chip-warn"
      style={{ marginLeft: 6 }}
      title="made before validation unlocked history: not a trial, not evidence, never shown to the advisor. Open data for why"
    >
      provisional
    </Link>
  );
}
