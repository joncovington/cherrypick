import { useSearchParams } from "react-router-dom";
import type { TradeSummaryPayload, TradeSummarySlice } from "@console/shared";
import { useMode } from "../../lib/useMode";
import { useTradeSummary } from "../../lib/api";
import { ModeToggle } from "../../components/ModeToggle";
import { ScopeSelect } from "../../components/ScopeBar";
import {
  RANGES,
  type Range,
  TsCard,
  fmtUsd,
  sessionLabel,
  NetMoneyCard,
  CaptureGaugeCard,
  ReturnOnRiskCard,
  WinRateCard,
  RangeSegments,
} from "../../components/tradeSummary/TradeSummaryCards";

/**
 * The Reports summary page (2026-10-11): the MEIC trade-summary surface, suite-wide. The MEIC
 * prototype stays the module-local deep view (legs, entry time, evaluations); this page carries
 * the cards that make sense for ANY module's ledger — money, capture, return on risk, win rate —
 * over `core.metrics summary` slices the server composes in ONE read
 * (readers/summary.ts, "The Reports summary's one read").
 *
 * Everything the page shows is already in the payload: the cards derive nothing beyond
 * presentation arithmetic (the win-rate ring counts green sessions off the payload's own daily
 * rows). Colour carries sign only. A null is never drawn as zero.
 *
 * `module=all` is a comparison, never a pool: one card per module, each module's own
 * denominators — pooling arms across modules would be a meaningless denominator.
 *
 * The session anchor is PER MODULE (flies/meic settle intraday, calendars/pmcc/curve/earnings
 * ride days): the active module's own facet list drives the day arrows, so switching modules
 * re-anchors the day stepping to that module's sessions. The day state is shared; the option
 * list is not.
 */
export function SummaryPage() {
  const [params, setParams] = useSearchParams();
  // The page rides the URL like every other page: ?mode, ?module, ?range, ?date, ?symbol, ?arm.
  // Anything unstated or unrecognised falls through to the defaults the same way the MEIC
  // prototype's range does.
  const statedRange = params.get("range");
  const range: Range = (RANGES as readonly string[]).includes(statedRange ?? "") ? (statedRange as Range) : "day";
  const statedModule = params.get("module");
  const moduleFilter: string | null = statedModule && statedModule !== "all" ? statedModule : null;
  const session = params.get("date");
  const symbol = params.get("symbol");
  const arm = params.get("arm");

  const setParam = (key: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null || v === "") next.delete(key);
    else next.set(key, v);
    setParams(next, { replace: true });
  };

  const [mode, setMode] = useMode();
  // The mode rides the query — a live ask must be a live READ, never a live badge over paper
  // figures (a module with no live ledger refuses in its own slice; the reader's rule).
  const q = useTradeSummary({ module: moduleFilter, mode, range, session, symbol, arm });
  const p = q.data ?? null;
  // The ACTIVE slice for the single-module view: the requested module's own, falling through to
  // the first readable slice when the request names no module.
  const active = p
    ? (moduleFilter !== null ? p.slices.find((s) => s.module === moduleFilter) : null) ??
      p.slices.find((s) => s.error === undefined) ??
      p.slices[0] ??
      null
    : null;
  const labelOf = (m: string): string => p?.returnLabels[m]?.capital ?? "capital at risk";

  // The daynav's label: a day names its session; an open window shows its resolved bounds (the
  // dates that exist in it), or its own name when the window is open-ended.
  const current = session ?? active?.window.from ?? null;
  const daynavLabel =
    range === "day"
      ? current !== null
        ? sessionLabel(current)
        : "…"
      : active != null
        ? windowAsLabel(active, range)
        : range;

  return (
    <div className="ts-root">
      {/* The toolbar: module segments, range segments, day arrows. The symbol and arm selects ride
          the scope row beside the mode toggle, their options taken from the ACTIVE module's own
          facets — facets of the rows in scope, never a hand-kept list. The module=all view has no
          single facet list, so the selects only narrow the single-module view. */}
      <div className="ts-toolbar">
        <h2 className="ts-title">trade summary</h2>
        <div className="ts-seg" role="tablist" aria-label="module">
          <button role="tab" aria-selected={moduleFilter === null} className={moduleFilter === null ? "ts-seg-on" : ""} onClick={() => setParam("module", null)}>
            all
          </button>
          {(p?.slices ?? []).map((s) => (
            <button key={s.module} role="tab" aria-selected={moduleFilter === s.module} className={moduleFilter === s.module ? "ts-seg-on" : ""} onClick={() => setParam("module", s.module)}>
              {s.module}
            </button>
          ))}
        </div>
        <RangeSegments range={range} onRange={(r) => setParam("range", r === "day" ? null : r)} />
        <div className="ts-daynav">
          <button aria-label="previous day" disabled={active === null} onClick={() => stepDay(active, session, -1, setParam)}>
            ‹
          </button>
          <span>{daynavLabel}</span>
          <button aria-label="next day" disabled={active === null || range !== "day"} title={range !== "day" ? "day stepping applies to the day range" : undefined} onClick={() => stepDay(active, session, 1, setParam)}>
            ›
          </button>
        </div>
      </div>

      {/* The scope row: mode, symbol, arm — and the modules that could not be read stay audible. */}
      <div className="ts-scope" role="group" aria-label="scope">
        <ModeToggle mode={mode} onChange={setMode} />
        <ScopeSelect label="symbol" value={symbol} options={active?.facets.symbols} onChange={(v) => setParam("symbol", v)} />
        <ScopeSelect label="arm" value={arm} options={active?.facets.arms} onChange={(v) => setParam("arm", v)} />
        {p && Object.keys(p.moduleErrors).length > 0 && (
          <span className="ts-muted" title={Object.entries(p.moduleErrors).map(([k, v]) => `${k}: ${v}`).join("\n")}>
            {Object.keys(p.moduleErrors).length} module{Object.keys(p.moduleErrors).length === 1 ? "" : "s"} unreadable
          </span>
        )}
        {/* Modules the switchboard turned off hold no comparison card — an off module is not part
            of the suite's day. One muted line names them and says they are reachable: the ask is
            a URL away, not a hidden absence. */}
        {p && p.modulesHidden.length > 0 && (
          <span className="ts-muted" title={`${p.modulesHidden.map((m) => `?module=${m}`).join("  ")} — each answers with its refusal`}
          >
            {p.modulesHidden.length} module{p.modulesHidden.length === 1 ? "" : "s"} off: {p.modulesHidden.join(", ")}
          </span>
        )}
      </div>

      {q.isError && <p className="ts-empty">the summary read failed — {q.error instanceof Error ? q.error.message : "unknown error"}</p>}
      {q.isLoading && <p className="ts-empty">reading the ledgers…</p>}

      {/* The single-module view: the module's four money cards. A module whose slice failed says
          so in its own line — never a wall of empty cards, never a wall of zeros. */}
      {p !== null && moduleFilter !== null && active !== null && (
        <ModuleCards s={active} capitalLabel={labelOf(active.module)} range={range} />
      )}

      {/* The comparison view (module=all): one card per module, the slices themselves. */}
      {p !== null && moduleFilter === null && (
        <div className="ts-row ts-row-2">
          {p.slices.map((s) => (
            <ComparisonCard key={s.module} s={s} capitalLabel={labelOf(s.module)} range={range} />
          ))}
        </div>
      )}
    </div>
  );
}

/* The day arrows step across the ACTIVE module's own sessions — the per-module session anchor.
 * The facet list is already filter-scoped (symbol/arm), so the arrows never offer a session the
 * current scope has no rows for. Stepping states ?date; clearing it falls back to the latest. */
function stepDay(
  active: TradeSummarySlice | null,
  session: string | null,
  delta: -1 | 1,
  setParam: (key: string, v: string | null) => void,
): void {
  if (active === null) return;
  const sessions = active.facets.sessions;
  if (sessions.length === 0) return;
  const current = session ?? active.window.from;
  const i = current === null ? -1 : sessions.indexOf(current);
  // Not in the list (or nothing stated): step to the newest/oldest session the scope knows.
  const next = i < 0 ? (delta < 0 ? sessions[sessions.length - 1] : sessions[0]) : sessions[Math.max(0, Math.min(sessions.length - 1, i + delta))];
  if (next !== undefined && next !== current) setParam("date", next);
}

/* A slice's window as a reader names it: a DAY names its session; an open window names its
 * resolved bounds (the dates that exist in it — 2026-10-06 → 2026-10-09), never its first
 * session dressed up as a day. Presentation arithmetic over the payload's own bounds. */
function windowAsLabel(s: TradeSummarySlice, range: Range): string {
  if (range === "day") return s.window.from !== null ? sessionLabel(s.window.from) : "the window";
  if (s.window.from != null && s.window.to != null) return `${s.window.from} → ${s.window.to}`;
  return range;
}

/* The single-module view: the four money cards — or one statement when the window has nothing
 * to show. A wall of zeros reads as "traded and broke even", which is a different claim from
 * "no rows in this window" and from "the read failed"; each gets its own line, never a zero. */
function ModuleCards({ s, capitalLabel, range }: { s: TradeSummarySlice; capitalLabel: string; range: Range }) {
  if (s.error !== undefined) return <p className="ts-empty">this module's read failed — {s.error}</p>;
  if (s.hero.trades === 0) return <p className="ts-empty">no rows in this window</p>;
  return (
    <div className="ts-row ts-row-4">
      <NetMoneyCard
        net={s.hero.net}
        gross={s.hero.gross}
        fees={s.hero.fees}
        settlement={null}
        openRisk={null}
        label={windowAsLabel(s, range)}
      />
      <CaptureGaugeCard capture={s.capture} trades={s.hero.trades} />
      <ReturnOnRiskCard
        ror={{
          capitalAtRisk: s.return.capitalAtRisk,
          returnOnRiskPct: s.return.returnOnRiskPct,
          returnOnRiskRawPct: s.return.returnOnRiskRawPct,
          coverage: s.return.coverage,
        }}
        capitalLabel={capitalLabel}
      />
      <WinRateCard trades={s.hero.trades} wins={s.hero.wins} daily={s.daily} />
    </div>
  );
}

/* One comparison card per module in the module=all view: the hero money row and the ROR figure
 * with its own denominator — the same figures the single-module view shows for that module, so
 * the two views cannot disagree. A module whose slice could not be read states its refusal.
 * Each module names its OWN resolved window: modules anchor sessions differently, so the
 * bounds can differ across the row, and each card says its own. */
function ComparisonCard({ s, capitalLabel, range }: { s: TradeSummarySlice; capitalLabel: string; range: Range }) {
  if (s.error !== undefined) {
    return (
      <TsCard title={s.module}>
        <p className="ts-empty">{s.error}</p>
      </TsCard>
    );
  }
  if (s.hero.trades === 0) {
    return (
      <TsCard title={s.module}>
        <p className="ts-empty">no rows in this window</p>
      </TsCard>
    );
  }
  const ror = s.return.returnOnRiskRawPct;
  return (
    <TsCard title={s.module} aside={<span className="ts-muted">{windowAsLabel(s, range)}</span>}>
      <div className={`ts-hero ${s.hero.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(s.hero.net)}</div>
      <dl className="ts-kv">
        <div><dt>trades</dt><dd>{s.hero.trades.toLocaleString("en-US")}</dd></div>
        <div><dt>win</dt><dd>{s.hero.winPct === null ? "n/r" : `${Math.round(s.hero.winPct)}%`}</dd></div>
        <div><dt>gross</dt><dd className="ts-pos">{fmtUsd(s.hero.gross)}</dd></div>
        <div><dt>fees</dt><dd className="ts-neg">−{fmtUsd(s.hero.fees).slice(1)}</dd></div>
        <div>
          <dt>return on {capitalLabel}</dt>
          <dd className={ror === null ? "" : ror >= 0 ? "ts-pos" : "ts-neg"}>{ror === null ? "n/r" : `${ror.toFixed(1)}%`}</dd>
        </div>
        <div><dt>coverage</dt><dd>{s.return.coverage}</dd></div>
      </dl>
    </TsCard>
  );
}
