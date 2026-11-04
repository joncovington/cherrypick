import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { BacktestGapStats, BacktestInventoryStore, BacktestOverview, BacktestSideReading, BacktestStore, BacktestValidation } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtGap } from "../../lib/bridged";
import { fmtCount } from "../../lib/format";
import { WithOverview, fmtBytes, mainTable, measure } from "./BacktestPage";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "./CardLightbox";
import { verdictChip } from "./studyIndex";

/**
 * The Backtest page's data tab as cards (2026-11-03): "is history trusted?" first and large, one row
 * per validation with a dot per check; the licence and the stores' totals beside it; then one tile per
 * store with the years its largest table covers. A validation opens its comparison tables in a
 * lightbox (checks, measures, quotes, GEX profile); a store opens what it holds, its tables, the code
 * that rebuilds it and the sessions its fetch refused (`?item=`).
 *
 * Every verdict is the module's: the unlock, each check within tolerance, each session's layer. The
 * page draws them; it counts nothing (a store's counts are the backtest-inventory job's).
 */

// ------------------------------------------------------------------ formatting

/** A fraction 0-1 as a percentage: `98.7%`. */
function frac(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

function gapCell(g: BacktestGapStats | null | undefined): string {
  if (g === null || g === undefined) return "—";
  return `${fmtGap(g.median ?? null)} / ${fmtGap(g.p90 ?? null)} / ${fmtGap(g.p99 ?? null)}`;
}

function pass(v: boolean | undefined | null): ReactNode {
  if (v === undefined || v === null) return <span className="muted">—</span>;
  return <span className={verdictChip(v ? "pass" : "fail")}>{v ? "pass" : "fail"}</span>;
}

function cadenceWord(c: number | null): string {
  return c === null ? "unknown cadence" : `${String(c)} s`;
}

// ------------------------------------------------------------------ the trust answer

/** The answer the tab leads with, from the module's unlock verdicts. */
export function trustAnswer(validation: BacktestValidation[]): { head: string; line: string } {
  const unlocked = validation.filter((v) => v.unlocks_history);
  if (validation.length === 0) return { head: "Not yet asked", line: "No validation has run. `python -m cherrypick.backtest validate` compares the vendor store against the recorder's own sessions." };
  if (unlocked.length > 0) {
    return {
      head: "Yes",
      line: `${String(unlocked.length)} of ${String(validation.length)} validation${validation.length === 1 ? "" : "s"} unlock${unlocked.length === 1 ? "s" : ""} history.`,
    };
  }
  return {
    head: "Not yet",
    line: `No: ${validation.length === 1 ? "the one validation does not unlock" : `none of the ${String(validation.length)} validations unlocks`} history, so every replay over it is provisional, not evidence.`,
  };
}

/** What stands in the way of one validation: the checks outside tolerance, or the session count. */
export function blocker(v: BacktestValidation): string | null {
  if (v.unlocks_history) return null;
  const failing = Object.entries(v.vendor_within_tolerance ?? {})
    .filter(([, ok]) => ok === false)
    .map(([n]) => n)
    .sort();
  return failing.length > 0 ? `Outside tolerance: ${failing.join(", ")}.` : "Every check is within tolerance; it needs more sessions compared.";
}

function checkNames(v: BacktestValidation): string[] {
  const names = new Set<string>([...Object.keys(v.vendor_at_least_recorder), ...Object.keys(v.recorder.checks ?? {}), ...Object.keys(v.vendor.checks ?? {})]);
  return [...names].sort();
}

// ------------------------------------------------------------------ the stores

/** One store tile: the inventory's record joined with the fetch's (refused sessions), by file. */
export interface StoreModel {
  file: string;
  holds: string | null;
  builtBy: string | null;
  script: string | null;
  usedBy: string | null;
  present: boolean;
  pending: boolean;
  bytes: number | undefined;
  /** The largest table's line, or the fetch's session count when there is no inventory. */
  main: string;
  span: [string, string] | null;
  tables: Array<[string, string]>;
  refused: number;
  reasons: Array<[string, number]>;
  error: string | null;
}

/** The stores, from the inventory when it was read, else from the replay stores' fetch record. */
export function storeModels(d: BacktestOverview): StoreModel[] {
  const fetched = new Map(d.stores.map((s) => [s.file, s]));
  const refusedOf = (s: BacktestStore | undefined) => ({
    refused: s?.refused ?? 0,
    reasons: Object.entries(s?.refused_reasons ?? {}).sort((a, b) => b[1] - a[1]),
  });
  if (!d.inventory) {
    return d.stores.map((s) => ({
      file: s.file,
      holds: `${s.symbol} at ${String(s.cadence)} s`,
      builtBy: null,
      script: null,
      usedBy: null,
      present: true,
      pending: false,
      bytes: undefined,
      main: `${fmtCount(s.complete)} sessions stored complete`,
      span: s.span,
      tables: [["sessions", `${fmtCount(s.complete)} complete`]],
      error: null,
      ...refusedOf(s),
    }));
  }
  return d.inventory.stores.map((s: BacktestInventoryStore) => {
    const tables = Object.entries(s.tables ?? {}).sort(([, a], [, b]) => b.rows - a.rows);
    return {
      file: s.file,
      holds: s.holds ?? null,
      builtBy: s.built_by ?? null,
      script: s.script ?? null,
      usedBy: s.used_by ?? null,
      present: s.present !== false,
      pending: s.pending === true,
      bytes: s.bytes,
      main: s.present === false ? "not on this machine" : mainTable(s),
      // The years covered: the largest table with a dated column (the largest may have none).
      span: tables.find(([, t]) => t.span !== null)?.[1].span ?? null,
      tables: tables.map(([name, t]) => [name, `${t.rows.toLocaleString("en-US")} rows${t.span ? ` · ${t.span[0]} to ${t.span[1]}` : ""}`]),
      error: s.error ?? null,
      ...refusedOf(fetched.get(s.file)),
    };
  });
}

/** The years the coverage bars share: the earliest span's start to the latest span's end. */
export function yearDomain(stores: StoreModel[]): [number, number] {
  const years = stores.flatMap((s) => (s.span ? [Number(s.span[0].slice(0, 4)), Number(s.span[1].slice(0, 4))] : [])).filter((y) => Number.isFinite(y));
  if (years.length === 0) return [2011, new Date().getFullYear()];
  return [Math.min(...years), Math.max(...years)];
}

/** Where a date sits between the domain's first and last year, as a percentage. */
function yearPct(date: string, [a, b]: [number, number]): number {
  const y = Number(date.slice(0, 4)) + (Number(date.slice(5, 7)) - 1) / 12;
  return Math.max(0, Math.min(100, ((y - a) / (b + 1 - a)) * 100));
}

export type StoreFilter = "all" | "present" | "missing" | "pending";

export function keepStore(s: StoreModel, f: StoreFilter): boolean {
  return f === "all" || (f === "present" && s.present) || (f === "missing" && !s.present) || (f === "pending" && s.pending);
}

function StoreTile({ s, domain, onOpen }: { s: StoreModel; domain: [number, number]; onOpen: () => void }) {
  const left = s.span ? yearPct(s.span[0], domain) : 0;
  const right = s.span ? yearPct(s.span[1], domain) : 0;
  return (
    <button type="button" className={s.present ? "study-tile d-store" : "study-tile d-store q-attn"} data-store={s.file} aria-label={`Open store ${s.file}`} onClick={onOpen}>
      <span className="study-tile-top">
        <span className="d-file">{s.file}</span>
        {!s.present ? <span className="chip chip-warn">missing</span> : s.pending ? <span className="chip q-waiting">count pending</span> : null}
      </span>
      <span className="study-tile-sub study-tile-clamp d-holds">{s.holds ?? "—"}</span>
      <span className="d-cover">
        <span className="q-meter-track d-track" aria-hidden="true">
          {s.span ? <span className="d-span" style={{ left: `${String(left)}%`, width: `${String(Math.max(1.5, right - left))}%` }} /> : null}
        </span>
        <span className="d-years">
          <span>{domain[0]}</span>
          <span>{s.span ? `${s.span[0].slice(0, 7)} → ${s.span[1].slice(0, 7)}` : s.present ? "no dated table" : "not on disk"}</span>
          <span>{domain[1]}</span>
        </span>
      </span>
      <span className="study-tile-sub">
        {[s.bytes !== undefined ? fmtBytes(s.bytes) : null, s.present ? s.main.split(" · ")[0] : "rebuild with the code inside", s.refused > 0 ? `${fmtCount(s.refused)} refused` : null]
          .filter((x): x is string => x !== null)
          .join(" · ")}
      </span>
    </button>
  );
}

// ------------------------------------------------------------------ the lightbox

export type DataItem = { kind: "validation"; key: string; v: BacktestValidation } | { kind: "store"; key: string; s: StoreModel };

type ValidationTab = "checks" | "measures" | "quotes" | "profile";
type StoreTab = "overview" | "tables" | "rebuild" | "refused";
export type DataTab = ValidationTab | StoreTab;

const VALIDATION_TABS: LightboxTab<ValidationTab>[] = [
  { id: "checks", label: "Checks" },
  { id: "measures", label: "Measures" },
  { id: "quotes", label: "Quotes" },
  { id: "profile", label: "GEX profile" },
];

const STORE_TABS: LightboxTab<StoreTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "tables", label: "Tables" },
  { id: "rebuild", label: "Rebuild" },
  { id: "refused", label: "Refused" },
];

function ChecksTable({ v }: { v: BacktestValidation }) {
  const names = checkNames(v);
  if (names.length === 0) return <p className="muted">No checks recorded.</p>;
  return (
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
  );
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

function QuotesTable({ v }: { v: BacktestValidation }) {
  const sessions = Object.entries(v.sessions).sort(([a], [b]) => a.localeCompare(b));
  if (sessions.length === 0) return <p className="muted">No sessions compared.</p>;
  return (
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
                  <td colSpan={5} className="muted">
                    {q?.reason ?? "not compared"}
                  </td>
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
  );
}

function ProfileTable({ v }: { v: BacktestValidation }) {
  const sessions = Object.entries(v.sessions).sort(([a], [b]) => a.localeCompare(b));
  if (sessions.length === 0) return <p className="muted">No sessions compared.</p>;
  return (
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
                  <td colSpan={6} className="muted">
                    {p?.reason ?? (p === undefined ? "not compared" : "no profiles paired")}
                  </td>
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
  );
}

function KeyValues({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="study-fields">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DataDialogBody({
  item,
  tab,
  onTab,
  position,
  onPrev,
  onNext,
  onClose,
}: {
  item: DataItem;
  tab: DataTab;
  onTab: (t: DataTab) => void;
  position: string;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  const nav = { position, onPrev, onNext, onClose };
  if (item.kind === "validation") {
    const v = item.v;
    const t: ValidationTab = (VALIDATION_TABS.some((x) => x.id === tab) ? tab : "checks") as ValidationTab;
    return (
      <LightboxFrame
        kicker={`Validation · ${v.file}`}
        title={`Vendor against the recorder, ${cadenceWord(v.cadence)}`}
        chips={
          <>
            <span className={v.unlocks_history ? "chip chip-ok" : "chip chip-warn"}>{v.unlocks_history ? "unlocks history" : "does not unlock history"}</span>
            <span className="chip muted">
              {v.sessions_compared ?? "—"} session(s) compared{v.dates.length > 0 ? ` · ${v.dates[0]!} → ${v.dates[v.dates.length - 1]!}` : ""}
            </span>
          </>
        }
        tabs={VALIDATION_TABS}
        tab={t}
        onTab={onTab}
        {...nav}
      >
        {blocker(v) ? <p className="study-decision">{blocker(v)}</p> : null}
        {t === "checks" ? <ChecksTable v={v} /> : null}
        {t === "measures" ? <MeasuresTable recorder={v.recorder} vendor={v.vendor} /> : null}
        {t === "quotes" ? <QuotesTable v={v} /> : null}
        {t === "profile" ? <ProfileTable v={v} /> : null}
      </LightboxFrame>
    );
  }
  const s = item.s;
  const t: StoreTab = (STORE_TABS.some((x) => x.id === tab) ? tab : "overview") as StoreTab;
  return (
    <LightboxFrame
      kicker={s.present ? "Store" : "Store · missing on this machine"}
      title={<span className="d-file">{s.file}</span>}
      chips={
        <>
          {!s.present ? <span className="chip chip-warn">missing</span> : null}
          {s.pending ? <span className="chip q-waiting">count pending</span> : null}
          {s.bytes !== undefined ? <span className="chip muted">{fmtBytes(s.bytes)}</span> : null}
        </>
      }
      tabs={STORE_TABS}
      tab={t}
      onTab={onTab}
      {...nav}
    >
      {s.error ? <p className="pnl-neg">{s.error}</p> : null}
      {t === "overview" ? (
        <>
          {s.holds ? <p className="study-decision">{s.holds}</p> : null}
          <KeyValues
            rows={[
              ["largest table", s.main],
              ["used by", s.usedBy ?? "—"],
              ["built by", s.builtBy ? <code key="b">{s.builtBy}</code> : "—"],
              ...(s.pending ? ([["count", "changed since the backtest-inventory job last counted it; it counts again on its next run"]] as Array<[string, ReactNode]>) : []),
            ]}
          />
        </>
      ) : null}
      {t === "tables" ? s.tables.length > 0 ? <KeyValues rows={s.tables} /> : <p className="muted">{s.present ? "No tables counted yet." : "None: the store is not on this machine."}</p> : null}
      {t === "rebuild" ? (
        <>
          {s.builtBy ? <pre className="d-cmd">{s.builtBy}</pre> : <p className="muted">No rebuild code recorded (the inventory could not be read).</p>}
          {s.script ? <KeyValues rows={[["script", <code key="s">{s.script}</code>]]} /> : null}
          <p className="muted">
            The vendor data is licensed: the backup skips it and <code>purge</code> removes it when the licence ends, so a store stays usable only
            because this code can rebuild it.
          </p>
        </>
      ) : null}
      {t === "refused" ? (
        s.refused === 0 ? (
          <p className="muted">The fetch refused no session.</p>
        ) : (
          <KeyValues rows={[["refused", `${fmtCount(s.refused)} session(s)`], ...s.reasons.map(([k, n]) => [k, `${fmtCount(n)}`] as [string, ReactNode])]} />
        )
      ) : null}
    </LightboxFrame>
  );
}

function DataDialog({ items, index, onStep, onClose }: { items: DataItem[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const [tab, setTab] = useState<DataTab>("checks");
  const item = items[index];
  if (item === undefined) return null;
  return (
    <CardLightbox count={items.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => <DataDialogBody item={item} tab={tab} onTab={setTab} position={nav.position} onPrev={nav.prev} onNext={nav.next} onClose={onClose} />}
    </CardLightbox>
  );
}

// ------------------------------------------------------------------ the tab

function LicenceTile({ d }: { d: BacktestOverview }) {
  const l = d.license;
  return (
    <section className="study-tile d-side">
      <span className="study-tile-sub">Licence</span>
      {l.active && l.terminated_on === null ? (
        <>
          <span className="d-big">Active</span>
          <span className="study-tile-sub">Active, with no end date set. When it ends, every vendor-derived file must be deleted by its purge date.</span>
        </>
      ) : (
        <>
          <span className={l.active ? "d-big" : "d-big pnl-neg"}>{l.active ? "active" : "terminated"}</span>
          <span className="study-tile-sub">
            terminated on {l.terminated_on ?? "—"} · purge by {l.purge_by ?? "—"} · {l.overdue ? "overdue" : l.days_left === null ? "—" : `${String(l.days_left)} days left`}
          </span>
          {l.overdue ? (
            <span className="study-tile-sub">
              The purge date has passed: <code>python -m cherrypick.backtest purge</code> lists what must go.
            </span>
          ) : null}
        </>
      )}
    </section>
  );
}

export function DataCards({ d }: { d: BacktestOverview }) {
  const [params, setParams] = useSearchParams();
  const lightbox = useOpenParam("item");
  const stores = storeModels(d);
  const raw = params.get("stores");
  const filter: StoreFilter = raw === "present" || raw === "missing" || raw === "pending" ? raw : "all";
  const shown = stores.filter((s) => keepStore(s, filter));
  const domain = yearDomain(stores);
  const items: DataItem[] = [
    ...d.validation.map((v): DataItem => ({ kind: "validation", key: `validation:${v.file}`, v })),
    ...shown.map((s): DataItem => ({ kind: "store", key: `store:${s.file}`, s })),
  ];
  const index = items.findIndex((i) => i.key === lightbox.open);
  const answer = trustAnswer(d.validation);
  const totalBytes = stores.reduce((n, s) => n + (s.bytes ?? 0), 0);
  const pick = (f: StoreFilter) => {
    const next = new URLSearchParams(params);
    if (f === "all") next.delete("stores");
    else next.set("stores", f);
    setParams(next, { replace: true });
  };
  const filters: Array<[StoreFilter, string]> = [
    ["all", `All ${String(stores.length)}`],
    ["present", "On disk"],
    ["missing", "Missing"],
    ["pending", "Count pending"],
  ];
  return (
    <>
      <div className="q-top">
        <section className="study-tile study-hero d-trust">
          <span className="study-tile-sub">Is history trusted?</span>
          <span className="study-hero-title">{answer.head}</span>
          <span className="d-line">{answer.line}</span>
          <span className="d-vals">
            {d.validation.map((v) => {
              const tol = Object.entries(v.vendor_within_tolerance ?? {}).sort(([a], [b]) => a.localeCompare(b));
              return (
                <button key={v.file} type="button" className="d-val" data-validation={v.file} aria-label={`Open validation ${v.file}`} onClick={() => lightbox.show(`validation:${v.file}`)}>
                  <span className="d-val-name">
                    <span className="d-file">{cadenceWord(v.cadence)}</span>
                    <span className="study-tile-sub">
                      {v.sessions_compared ?? "—"} session(s) compared{v.dates.length > 0 ? ` · ${v.dates[0]!} → ${v.dates[v.dates.length - 1]!}` : ""}
                    </span>
                  </span>
                  <span className="study-tile-sub d-val-why">{blocker(v) ?? "unlocks history"}</span>
                  <span className="d-dots" aria-label={`${String(tol.filter(([, ok]) => ok).length)} of ${String(tol.length)} checks within tolerance`}>
                    {tol.map(([n, ok]) => (
                      <span key={n} className={ok ? "d-dot is-in" : "d-dot is-out"} title={`${n}: ${ok ? "within tolerance" : "outside tolerance"}`} />
                    ))}
                  </span>
                </button>
              );
            })}
          </span>
          {d.validation.length > 0 ? (
            <span className="d-legend">
              <span>
                <span className="d-dot is-in" /> within tolerance
              </span>
              <span>
                <span className="d-dot is-out" /> outside tolerance
              </span>
              <span>A validation unlocks once every check is within tolerance over enough sessions.</span>
            </span>
          ) : null}
        </section>
        <div className="q-side">
          <LicenceTile d={d} />
          <section className="study-tile d-side">
            <span className="study-tile-sub">Stores</span>
            <span className="d-totals">
              <span>
                <strong>{stores.filter((s) => s.present).length}</strong> on disk
              </span>
              <span>
                <strong className={stores.some((s) => !s.present) ? "d-warn" : undefined}>{stores.filter((s) => !s.present).length}</strong> missing
              </span>
              {totalBytes > 0 ? (
                <span>
                  <strong>{fmtBytes(totalBytes)}</strong> in all
                </span>
              ) : null}
            </span>
          </section>
        </div>
      </div>

      <section className="q-section">
        <div className="d-head">
          <h3 className="q-section-title">Every store, and the code that rebuilds it</h3>
          <div className="history-controls" role="group" aria-label="Show stores">
            {filters.map(([f, label]) => (
              <button key={f} type="button" className={filter === f ? "chip chip-ok" : "chip"} aria-pressed={filter === f} onClick={() => pick(f)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {d.inventory?.error ? <p className="pnl-neg">{d.inventory.error}</p> : null}
        {d.inventory && d.inventory.undeclared.length > 0 ? (
          <p className="pnl-neg">
            Undeclared: {d.inventory.undeclared.map((u) => u.file).join(", ")}. Add each to <code>stores.py</code> with the code that builds it.
          </p>
        ) : null}
        {stores.length === 0 ? (
          <p className="muted">No vendor store on this machine yet.</p>
        ) : shown.length === 0 ? (
          <p className="muted">No store matches this filter.</p>
        ) : (
          <div className="study-tiles q-tiles">
            {shown.map((s) => (
              <StoreTile key={s.file} s={s} domain={domain} onOpen={() => lightbox.show(`store:${s.file}`)} />
            ))}
          </div>
        )}
      </section>
      {index >= 0 ? <DataDialog items={items} index={index} onStep={(to) => lightbox.step(items[to]?.key ?? "")} onClose={lightbox.close} /> : null}
    </>
  );
}

export function BacktestDataTab() {
  return (
    <WithOverview>
      {(d, updatedAt) => (
        <div className="cards cards-wide">
          <Card title="Data" collapseKey="backtest-data" updatedAt={updatedAt}>
            <DataCards d={d} />
            <p className="muted" style={{ marginTop: 8 }}>
              The vendor data is licensed: the backup skips it and <code>purge</code> removes it when the licence ends. What keeps a store usable
              is that it can be rebuilt, so each one names its fetch code (<code>packages/backtest/stores.py</code>). A validation compares the
              vendor store against the recorder&apos;s own sessions; open one for its tables.
            </p>
          </Card>
        </div>
      )}
    </WithOverview>
  );
}
