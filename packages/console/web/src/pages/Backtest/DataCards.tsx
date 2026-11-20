import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { BacktestInventoryStore, BacktestOverview, BacktestStore } from "@console/shared";
import { HelpSection } from "../../components/CardHelp";
import { Card } from "../../components/DataTable";
import { ListViewContext, ViewSwitch, useListView, useViewChoice } from "../../components/ViewSwitch";
import { BtRows, type BtRow } from "./BtRows";
import { fmtCount } from "../../lib/format";
import { WithOverview, fmtBytes, mainTable } from "./BacktestPage";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "./CardLightbox";
import { GuideLink } from "../../components/GuideLink";
import { FullSetCard } from "./FullSetCard";

/**
 * The Backtest page's data tab as cards: the licence and the stores' totals first, then one tile per
 * store with the years its largest table covers. A store opens what it holds, its tables, the code
 * that rebuilds it and the sessions its fetch refused (`?item=`).
 *
 * The page draws what the module wrote; it counts nothing (a store's counts are the
 * backtest-inventory job's).
 */

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

/** A store's tile on one line: its state, its file, what it holds, its span and size. */
export function storeRow(s: StoreModel, onOpen: () => void): BtRow {
  const state = !s.present ? <span className="chip chip-warn">missing</span> : s.pending ? <span className="chip q-waiting">count pending</span> : <span className="chip chip-ok">on disk</span>;
  const size = [s.bytes !== undefined ? fmtBytes(s.bytes) : null, s.present ? s.main.split(" · ")[0] : "rebuild with the code inside", s.refused > 0 ? `${fmtCount(s.refused)} refused` : null];
  return {
    key: s.file,
    label: `Open store ${s.file}`,
    lead: state,
    title: <span className="d-file">{s.file}</span>,
    meta: s.holds ?? "—",
    end: [s.span ? `${s.span[0].slice(0, 7)} → ${s.span[1].slice(0, 7)}` : s.present ? "no dated table" : "not on disk", ...size].filter((x): x is string => x !== null && x !== undefined).join(" · "),
    attention: !s.present,
    data: { "data-store": s.file },
    onOpen,
  };
}

// ------------------------------------------------------------------ the lightbox

export interface DataItem {
  key: string;
  s: StoreModel;
}

export type DataTab = "overview" | "tables" | "rebuild" | "refused";

const STORE_TABS: LightboxTab<DataTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "tables", label: "Tables" },
  { id: "rebuild", label: "Rebuild" },
  { id: "refused", label: "Refused" },
];

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
  const s = item.s;
  const t: DataTab = STORE_TABS.some((x) => x.id === tab) ? tab : "overview";
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
  const [tab, setTab] = useState<DataTab>("overview");
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
  // Rows apply to the store list; the licence and totals panels above it stay.
  const view = useListView();
  const lightbox = useOpenParam("item");
  const stores = storeModels(d);
  const raw = params.get("stores");
  const filter: StoreFilter = raw === "present" || raw === "missing" || raw === "pending" ? raw : "all";
  const shown = stores.filter((s) => keepStore(s, filter));
  const domain = yearDomain(stores);
  const items: DataItem[] = shown.map((s) => ({ key: `store:${s.file}`, s }));
  const index = items.findIndex((i) => i.key === lightbox.open);
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
      <div className="d-top">
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
        ) : view === "rows" ? (
          <BtRows rows={shown.map((s) => storeRow(s, () => lightbox.show(`store:${s.file}`)))} />
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
  const [view, setView] = useViewChoice("backtest-data");
  return (
    <WithOverview>
      {(d, updatedAt) => (
        <div className="cards cards-wide">
          <FullSetCard />
          <Card
            title="Data"
            collapseKey="backtest-data"
            updatedAt={updatedAt}
            controls={<ViewSwitch view={view} onChange={setView} />}
            help={{
              title: "Reading the data tab",
              body: (
                <>
                  <HelpSection title="Rebuilding">
                    <p>
                      The vendor data is licensed: the backup skips it and <code>purge</code> removes it when the licence ends. What keeps a
                      store usable is that it can be rebuilt, so each one names its fetch code (<code>packages/backtest/stores.py</code>).
                    </p>
                  </HelpSection>
                  <HelpSection title="Cards or rows">
                    <p>The switch in the card head lists the stores as rows, one line a store; a row opens the same store.</p>
                  </HelpSection>
                  <GuideLink to="/help/research-and-backtests#where-the-data-and-results-live" />
                </>
              ),
            }}
          >
            <ListViewContext.Provider value={view}>
              <DataCards d={d} />
            </ListViewContext.Provider>
          </Card>
        </div>
      )}
    </WithOverview>
  );
}
