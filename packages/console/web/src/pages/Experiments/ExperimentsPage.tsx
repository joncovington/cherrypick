import { Link } from "react-router-dom";
import type { ModuleSummarySlice, TradingMode } from "@console/shared";
import { StatusLine, useModuleSummary } from "../../components/summary/ModuleSummary";
import { fmtUsd } from "../../components/summary/kit";
import { etToday } from "../../components/table/DateRange";

/**
 * The Experiments page (2026-10-30, docs/console-review-plan.md batch 4): the four paper-only
 * experiments as one page, a card each, and the retired flies module's record. Each card reads the
 * module's own summary (`/api/summary/:module`) twice -- all time, and its latest traded day -- and
 * says what history found; the module's own pages hold everything else and the card links to them.
 * The modules leave the rail; the rail opens one out while you are on it.
 */

export const EXPERIMENTS = [
  { id: "pmcc", label: "PMCC", what: "a deep in-the-money long call with short calls sold against it" },
  { id: "calendars", label: "Calendars", what: "a weekly SPY double calendar" },
  { id: "curve", label: "Curve", what: "VXX call credit spreads on a contango day" },
  { id: "contango", label: "Contango", what: "SVXY or SHV, switched on the VIX/VIX3M ratio" },
] as const;

const PAGES = [
  ["summary", "today"],
  ["positions", "open"],
  ["history", "closed"],
  ["performance", "performance"],
] as const;

function Figure({ label, value, tone, foot }: { label: string; value: string; tone?: "pos" | "neg"; foot?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className={`stat-value${tone ? ` pnl-${tone}` : ""}`}>{value}</span>
      {foot && <span className="stat-foot muted">{foot}</span>}
    </div>
  );
}

function net(s: ModuleSummarySlice | undefined): { value: string; tone?: "pos" | "neg" } {
  if (s === undefined) return { value: "…" };
  if (s.hero.trades === 0) return { value: "—" };
  return { value: fmtUsd(s.hero.net), tone: s.hero.net >= 0 ? "pos" : "neg" };
}

/** The latest traded day, when it is not today: the day a summary with no date resolves to. */
export function lastTradedOf(day: ModuleSummarySlice | undefined, today: string): string | null {
  const from = day?.window.label === "day" ? day.window.from : null;
  return from !== null && from !== undefined && from < today ? from : null;
}

export function ExperimentCard({ id, label, what }: { id: string; label: string; what: string }) {
  const all = useModuleSummary(id, "paper", "all", null);
  const day = useModuleSummary(id, "paper", "day", null);
  const a = all.data;
  const lastTraded = lastTradedOf(day.data, etToday());
  const n = net(a);
  return (
    <section className="card" data-experiment={id}>
      <div className="card-head">
        <h2>{label}</h2>
        <span className="chip">paper</span>
        <span className="card-asof">{what}</span>
      </div>
      <StatusLine module={id} mode="paper" lastTraded={lastTraded} />
      {all.isError ? (
        <p className="muted">The summary could not be read.</p>
      ) : (
        <div className="stat-row">
          <Figure label="net, all time" value={n.value} tone={n.tone} foot={a ? `${String(a.hero.trades)} settled, after every cost` : undefined} />
          {/* The last traded day is the status line's, above: a tile would say it twice. */}
          <Figure label="open now" value={a ? String(a.hero.open) : "…"} />
        </div>
      )}
      <p className="experiment-links">
        {PAGES.map(([slide, name], i) => (
          <span key={slide}>
            {i > 0 ? " · " : ""}
            <Link to={`/${id}/${slide}`}>{name}</Link>
          </span>
        ))}
      </p>
    </section>
  );
}

function FliesRecord() {
  const paper = useModuleSummary("flies", "paper", "all", null);
  const live = useModuleSummary("flies", "live" as TradingMode, "all", null);
  const p = net(paper.data);
  const l = net(live.data);
  return (
    <section className="card" data-experiment="flies">
      <div className="card-head">
        <h2>Flies</h2>
        <span className="chip">retired</span>
        <span className="card-asof">0DTE SPX net-credit butterflies</span>
      </div>
      <p className="muted">
        <strong>Retired by the developer in the live config on 2026-10-19; may be reinstated at a later date</strong>{" "}
        (packages/flies/CLAUDE.md). Its pages are off while the module is; its arms stay on{" "}
        <Link to="/compare/arms">Compare</Link>.
      </p>
      <div className="stat-row">
        <Figure label="paper net, all time" value={p.value} tone={p.tone} foot={paper.data ? `${String(paper.data.hero.trades)} settled` : undefined} />
        <Figure label="live pilot net" value={l.value} tone={l.tone} foot={live.data ? `${String(live.data.hero.trades)} settled` : undefined} />
      </div>
    </section>
  );
}

export function ExperimentsPage() {
  return (
    <div className="cards cards-wide">
      <p className="muted" style={{ margin: 0 }}>
        Paper-only experiments, a card each: what history found and where it is written, the money so far, and links into
        the module's own pages.
      </p>
      {EXPERIMENTS.map((e) => (
        <ExperimentCard key={e.id} id={e.id} label={e.label} what={e.what} />
      ))}
      <FliesRecord />
    </div>
  );
}
