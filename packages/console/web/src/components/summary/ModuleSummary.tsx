import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { ModuleSummarySlice, SummaryBreakdown, SummaryTile, SummaryUnit, TradingMode } from "@console/shared";
import { SortTh, sortRows, useSort } from "../DataTable";
import { Gauge, RangeToolbar, TsCard, TsEmpty, WinRateArcs, fmtUsd, fmtUsdPlain, sessionLabel, useSummaryRange } from "./kit";

/**
 * A module's simplified-view summary: the MEIC summary's pattern over `/api/summary/:module`.
 * The component is shared; WHAT it shows is the module reader's choice (readers/summary/), in the
 * module's own vocabulary. It derives nothing but presentation. Loading, error and empty are
 * three different statements, never a wall of zeros.
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

function NetCard({ s, label }: { s: ModuleSummarySlice; label: string }) {
  const { hero } = s;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{label}</span>}>
      <div className={`ts-hero ${hero.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(hero.net)}</div>
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
    <section className="ts-card">
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
    </section>
  );
}

function Breakdown({ b }: { b: SummaryBreakdown }) {
  if (b.rows.length === 0) return <TsEmpty what={b.title} />;
  const max = Math.max(...b.rows.map((r) => Math.abs(r.net)), 1);
  return (
    <TsCard title={b.title}>
      <div className="table-scroll">
      <table className="ts-table">
        <thead>
          <tr>
            <th>{b.column}</th>
            <th className="num">n</th>
            <th className="num">win</th>
            <th className="num">avg</th>
            <th className="num">net</th>
            <th aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {b.rows.map((r) => (
            <tr key={r.bucket}>
              <td>{r.bucket}</td>
              <td className="num">{r.trades}</td>
              <td className="num">{r.winPct === null ? "n/r" : `${r.winPct.toFixed(0)}%`}</td>
              <td className={`num ${r.avgNet === null ? "" : r.avgNet >= 0 ? "ts-pos" : "ts-neg"}`}>{r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}</td>
              <td className={`num ${r.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(r.net)}</td>
              <td style={{ width: "22%" }}>
                <div className="ts-hbar">
                  <div className={`ts-hbar-fill ${r.net >= 0 ? "ts-pos" : "ts-neg"}`} style={{ width: `${((Math.abs(r.net) / max) * 100).toFixed(1)}%` }} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </TsCard>
  );
}

function ByArm({ s, arm }: { s: ModuleSummarySlice; arm?: string | null }) {
  const sort = useSort();
  if (s.arms.length === 0) return <TsEmpty what="by arm" />;
  const arms = sortRows(s.arms, sort, {
    arm: (a) => a.arm,
    n: (a) => a.trades,
    win: (a) => a.winPct,
    net: (a) => a.net,
  });
  return (
    <TsCard title="by arm" aside={<span className="ts-muted">{arm != null && arm !== "" ? `the ${arm} arm` : "every arm in the window"}</span>}>
      <div className="table-scroll">
      <table className="ts-table">
        <thead>
          <tr>
            <SortTh label="arm" k="arm" sort={sort} />
            <SortTh label="n" k="n" sort={sort} className="num" />
            <SortTh label="win" k="win" sort={sort} className="num" />
            <SortTh label="net" k="net" sort={sort} className="num" />
          </tr>
        </thead>
        <tbody>
          {arms.map((a) => (
            <tr key={a.arm} className={a.retired ? "ts-muted" : undefined}>
              <td>
                {a.arm}
                {a.retired ? " (retired)" : ""}
              </td>
              <td className="num">{a.trades}</td>
              <td className="num">{a.winPct === null ? "n/r" : `${a.winPct.toFixed(0)}%`}</td>
              <td className={`num ${a.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(a.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </TsCard>
  );
}

export function ModuleSummary({
  module,
  mode,
  scope,
  day,
  onDay,
}: {
  module: string;
  mode: TradingMode;
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
  const label = s === null ? "…" : s.window.label === "day" && s.window.from !== null ? sessionLabel(s.window.from) : s.window.label;
  return (
    <div className="ts-root">
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
          {s.unit && <p className="ts-muted ts-unit">one trade here is {s.unit}</p>}
          {s.hero.trades === 0 && s.hero.open === 0 ? (
            <p className="ts-empty">no settled trades in this window</p>
          ) : (
            <>
              <div className={`ts-row ${s.gauges.length >= 2 ? "ts-row-4" : s.gauges.length === 1 ? "ts-row-3" : "ts-row-2"}`}>
                <NetCard s={s} label={label} />
                {s.gauges.map((g) => (
                  <Gauge key={g.title} title={g.title} rawPct={g.rawPct} min={g.min} max={g.max} caption={g.caption} />
                ))}
                <WinRateArcs daysWon={s.hero.daysWon} days={s.hero.daysWon + s.hero.daysLost} wins={s.hero.wins} trades={s.hero.trades} />
              </div>
              <div className="ts-row">
                <Tiles tiles={s.tiles} />
              </div>
              {s.breakdowns.length > 0 && (
                <div className={`ts-row ${s.breakdowns.length >= 3 ? "ts-row-3" : "ts-row-2"}`}>
                  {s.breakdowns.map((b) => (
                    <Breakdown key={b.title} b={b} />
                  ))}
                </div>
              )}
              <div className="ts-row">
                <ByArm s={s} arm={scope?.arm ?? null} />
              </div>
            </>
          )}
          {s.notes.length > 0 && (
            <ul className="ts-notes ts-muted">
              {s.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
