import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { ModuleSummarySlice, SummaryBreakdown, SummaryTile, SummaryUnit, TradingMode } from "@console/shared";
import { Gauge, RangeToolbar, TsCard, TsEmpty, WinRateArcs, fmtUsd, fmtUsdPlain, sessionLabel, useSummaryRange } from "./kit";

/**
 * A module's simplified-view summary: the MEIC summary's pattern over `/api/summary/:module`.
 * The component is shared; WHAT it shows is the module reader's choice (readers/summary/), in the
 * module's own vocabulary. It derives nothing but presentation. Loading, error and empty are
 * three different statements, never a wall of zeros.
 */

export function useModuleSummary(module: string, mode: TradingMode, range: string, day: string | null) {
  const params = new URLSearchParams({ mode, range });
  if (day !== null) params.set("date", day);
  return useQuery<ModuleSummarySlice>({
    queryKey: ["module-summary", module, mode, range, day],
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
    </TsCard>
  );
}

function ByArm({ s }: { s: ModuleSummarySlice }) {
  if (s.arms.length === 0) return <TsEmpty what="by arm" />;
  return (
    <TsCard title="by arm" aside={<span className="ts-muted">every arm in the window</span>}>
      <table className="ts-table">
        <thead>
          <tr>
            <th>arm</th>
            <th className="num">n</th>
            <th className="num">win</th>
            <th className="num">net</th>
          </tr>
        </thead>
        <tbody>
          {s.arms.map((a) => (
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
    </TsCard>
  );
}

export function ModuleSummary({ module, mode }: { module: string; mode: TradingMode }) {
  const [range, setRange] = useSummaryRange();
  const [params, setParams] = useSearchParams();
  const stated = params.get("day");
  const day = stated !== null && /^\d{4}-\d{2}-\d{2}$/.test(stated) ? stated : null;
  const q = useModuleSummary(module, mode, range, day);
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
                <ByArm s={s} />
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
