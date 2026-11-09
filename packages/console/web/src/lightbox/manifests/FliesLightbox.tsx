import { useEffect, useState } from "react";
import { ModuleCosts } from "../../components/costs/ModuleCosts";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useFlies, useFliesMeta, fliesQuery, type FliesFilter } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill, usePage } from "../../components/ScopeBar";
import { ModuleIntegrityStrip } from "../../components/ModuleIntegrityStrip";
import type { TradingMode } from "@console/shared";
import { ForestCard } from "../../pages/Flies/ForestCard";
import { AttemptsTab } from "../../components/AttemptsTab";
import { TimelineCard } from "../../pages/Flies/TimelineCard";
import { HistoryTab } from "../../pages/Flies/HistoryTab";
import { JournalCard } from "../../pages/Flies/JournalCard";
import { OpeningRangeCard } from "../../pages/Flies/OpeningRangeCard";
import { GatesSlide } from "../../pages/Flies/GatesSlide";
import { LiveGapSlide } from "../../pages/Flies/LiveGapSlide";
import { SelectorSlide } from "../../pages/Flies/SelectorSlide";
import { PerformanceTab } from "../../pages/Flies/PerformanceTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AnalysisSlide } from "../../components/analysis/AnalysisSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ExperimentGuideView } from "../../components/ExperimentGuide";
import { BooksCard, PositionsCard } from "../../pages/Flies/FliesTables";
import { FliesScopeFilter, FliesScopeSummary, NO_SCOPE, type FliesScope } from "../../pages/Flies/FliesFilter";
import { RegimeCutsTab } from "../../components/RegimeCutsTab";
import { ModuleFrame } from "../ModuleFrame";
import { FLIES_SLIDES, type FliesSlideId } from "../navGroups";
import { FliesSession } from "../../pages/Flies/FliesSession";
import { AgentSlide } from "../../pages/Flies/AgentSlide";
import type { SlideDef } from "../types";
import { ModuleSummary } from "../../components/summary/ModuleSummary";

/**
 * Labels come from the nav declaration, not from here. The rail is built from that file and the
 * breadcrumb from this one, so a label typed twice is a label that can disagree with itself; the
 * ids are typed against it too, which is what stops a tab being added here and never appearing in
 * the rail.
 */
const FLIES_LABEL = Object.fromEntries(FLIES_SLIDES.map((s) => [s.id, s.label])) as Record<
  FliesSlideId,
  string
>;

interface FliesLoopStatus {
  state: "live" | "idle" | "no-data";
  lastIterationAt: string | null;
  ageSeconds: number | null;
  symbol: string | null;
  arm: string | null;
  underlyingPrice: number | null;
}

interface FliesAnalytics {
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
    completed: number;
    maxPossibleLoss: number;
    sessionPeakWorst: { worst: number; at: string } | null;
    settlement?: { price: number; source: string | null; at: string | null } | null;
    medianCompletionMin?: number | null;
  };
  byArm: Array<{ arm: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  feeDrag: Array<{ arm: string; premium: number; gross: number; fees: number; settlementFees: number | null; net: number; dragPct: number | null }>;
}

function useFliesAnalytics(mode: TradingMode, filter: FliesFilter) {
  return useQuery<FliesAnalytics>({
    queryKey: ["flies-analytics", mode, filter],
    queryFn: async () => {
      const res = await fetch(`/api/flies/analytics?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`flies analytics: HTTP ${res.status}`);
      return (await res.json()) as FliesAnalytics;
    },
    refetchInterval: 30_000,
  });
}

function emptyReason(a: FliesAnalytics | undefined): string {
  const open = a?.today.positions ?? 0;
  if (open === 0) return "no positions on this session";
  return `${open} position${open === 1 ? "" : "s"} entered, none settled yet — per-arm results fill in as the book settles`;
}

export function FliesLightbox({ slide }: { slide: string }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useMode();
  const [scope, setScope] = useState<FliesScope>(NO_SCOPE);
  const [scopeOpen, setScopeOpen] = useState(false);
  const { arm, date, era, symbol } = scope;
  const setDate = (d: string | null) => setScope((s) => ({ ...s, date: d }));
  const meta = useFliesMeta(mode, era);
  const resolvedDate = date ?? meta.data?.dates[0] ?? null;
  const filter: FliesFilter = { arm, date: resolvedDate, symbol, era };
  const multiDayFilter: FliesFilter = { arm, date, symbol, era };
  const booksPage = usePage([mode, arm, date, symbol, era]);
  const positionsPage = usePage([mode, arm, date, symbol, era]);
  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useFlies(mode, filter, booksPage.page, positionsPage.page);
  const analytics = useFliesAnalytics(mode, filter);
  const a = analytics.data;
  const loop = useQuery<FliesLoopStatus>({
    queryKey: ["flies-loop", mode],
    queryFn: async () => (await fetch(`/api/flies/loop?mode=${mode}`)).json() as Promise<FliesLoopStatus>,
    refetchInterval: 30_000,
  });
  const l = loop.data;

  useEffect(() => {
    if (meta.data === undefined) return;
    // A pick the era's own options no longer hold would select nothing: back to the default.
    const m = meta.data;
    setScope((s) => {
      const next = {
        ...s,
        arm: s.arm !== null && !m.arms.includes(s.arm) ? null : s.arm,
        date: s.date !== null && !m.dates.includes(s.date) ? null : s.date,
        symbol: s.symbol !== null && !m.symbols.includes(s.symbol) ? null : s.symbol,
      };
      return next.arm === s.arm && next.date === s.date && next.symbol === s.symbol ? s : next;
    });
  }, [meta.data]);

  const replayDay = (d: string) => {
    setDate(d);
    // Picking a day out of the history table is a request to go look at it: the session page.
    const qs = params.toString();
    navigate(`/flies/session${qs ? `?${qs}` : ""}`);
  };

  const slides: Array<SlideDef & { id: FliesSlideId }> = [
    {
      id: "summary",
      label: FLIES_LABEL.summary,
      // The page header owns the scope: its arm/symbol/era narrow the summary, and its date select
      // and the summary's day arrows step the same session. Before this the summary ignored all
      // four and read its own ?day=, so the header looked dead on this tab.
      render: () => (
        <ModuleSummary
          module="flies"
          mode={mode}
          scope={{ arm, symbol, era }}
          day={date}
          onDay={(d) => setDate(d)}
        />
      ),
    },
    {
      id: "session",
      label: FLIES_LABEL.session,
      render: () => (
        <FliesSession
          mode={mode}
          filter={filter}
          arm={arm}
          analytics={a}
          loading={analytics.isLoading}
        />
      ),
    },
    {
      id: "forest",
      label: FLIES_LABEL.forest,
      render: () => <ForestCard mode={mode} filter={filter} />,
    },
    {
      // Attempts and occupancy merged (2026-09): both are bounded snapshots regardless of session
      // activity -- AttemptTimeline's SVG height depends only on arm count (marks position by time
      // on a fixed-width axis, not by attempt index), and OccupancyMap shows only CURRENTLY open
      // legs (a snapshot table, not a growing log of every leg touched today) -- so combining them
      // does not risk outgrowing the lightbox body on a busy session the way the journal/timeline
      // tabs' genuinely unbounded per-event content would.
      id: "attempts",
      label: FLIES_LABEL.attempts,
      render: () => (
        <AttemptsTab module="flies" mode={mode} date={filter.date} scope={{ arm, symbol, era }} />
      ),
    },
    { id: "timeline", label: FLIES_LABEL.timeline, render: () => <TimelineCard mode={mode} filter={filter} arm={arm} /> },
    { id: "openingrange", label: FLIES_LABEL.openingrange, render: () => <OpeningRangeCard filter={filter} /> },
    { id: "decisions", label: FLIES_LABEL.decisions, render: () => <JournalCard mode={mode} filter={filter} /> },
    { id: "gates", label: FLIES_LABEL.gates, render: () => <GatesSlide /> },
    { id: "livegap", label: FLIES_LABEL.livegap, render: () => <LiveGapSlide /> },
    { id: "selector", label: FLIES_LABEL.selector, render: () => <SelectorSlide /> },
    { id: "completion", label: FLIES_LABEL.completion, render: () => <PerformanceTab mode={mode} filter={multiDayFilter} /> },
    { id: "advisor", label: FLIES_LABEL.advisor, render: () => <AdvisorSlide module="flies" /> },
    { id: "performance", label: FLIES_LABEL.performance, render: () => <PerformanceSlide module="flies" mode={mode} arm={arm} /> },
    { id: "analysis", label: FLIES_LABEL.analysis, render: () => <AnalysisSlide module="flies" mode={mode} arm={arm} /> },
    { id: "costs", label: FLIES_LABEL.costs, render: () => <ModuleCosts module="flies" mode={mode} era={era} arm={arm} /> },
    { id: "regime", label: FLIES_LABEL.regime, render: () => <RegimeCutsTab module="flies" arm={arm} /> },
    { id: "agent", label: FLIES_LABEL.agent, render: () => <AgentSlide mode={mode} date={date} /> },
    // The three dense tables (2026-09-24: pages in the rail's `tables` group, where they had been
    // overlay sheets). Same markup, pagers and queries as before -- they moved rather than changed.
    // The session cards whose numbers they explain link here: net today to history, net by arm to
    // positions, fee drag and the payoff hero to books.
    {
      id: "books",
      label: FLIES_LABEL.books,
      render: () => <BooksCard data={data} paging={booksPage} loading={isLoading} isError={isError} busy={isPlaceholderData} narrowed={arm !== null || date !== null} />,
    },
    {
      id: "positions",
      label: FLIES_LABEL.positions,
      render: () => <PositionsCard data={data} paging={positionsPage} loading={isLoading} isError={isError} busy={isPlaceholderData} narrowed={arm !== null || date !== null} />,
    },
    {
      id: "history",
      label: FLIES_LABEL.history,
      render: () => <HistoryTab mode={mode} filter={multiDayFilter} onReplayDay={replayDay} />,
    },
    {
      id: "guide",
      label: FLIES_LABEL.guide,
      render: () => (
        <ExperimentGuideView
          url="/api/flies/arms"
          mode={mode}
          intro="Every arm is an independent portfolio trading the same market with the same money, so the only thing separating them is which entries their rules allow. Each description below is the module's own — read from the deployed config — and 'what makes it different' is derived from the arm's settings: the values it does not share with the defaults or with most of its siblings."
        />
      ),
    },
  ];

  // The scope's summary line sits over each tab it governs; the experiment guide reads the config, not
  // a session, so it has none.
  const scoped = slides.map((sl) =>
    sl.id === "guide"
      ? sl
      : {
          ...sl,
          render: () => (
            <>
              <FliesScopeSummary
                scope={scope}
                meta={meta.data}
                note={l?.underlyingPrice != null ? `spot ${l.underlyingPrice.toFixed(2)}` : null}
                onOpen={() => setScopeOpen(true)}
                onReset={() => setScope(NO_SCOPE)}
              />
              {sl.render()}
            </>
          ),
        },
  );

  return (
    <ModuleFrame
      module="flies"
      slide={slide}
      slides={scoped}
      badge={<PaperLiveBadge mode={mode} />}
      session={null}
      loopPill={
        <LoopPill
          state={l?.state}
          ageSeconds={l?.ageSeconds}
          detail={l?.lastIterationAt != null ? `last iteration ${l.lastIterationAt}${l.arm !== null ? ` · ${l.arm}` : ""}` : undefined}
        />
      }
      headerControls={
        <>
          <FliesScopeFilter scope={scope} meta={meta.data} onChange={setScope} open={scopeOpen} onOpenChange={setScopeOpen} />
          {l?.underlyingPrice != null && <span className="chip">{l.underlyingPrice.toFixed(2)}</span>}
          <ModeToggle mode={mode} onChange={setMode} />
        </>
      }
      integrity={<ModuleIntegrityStrip integrity={data?.integrity} collapseKey="flies-integrity" updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity?.measurementBreaks.length ?? 0) > 0 || (data?.integrity?.schemaDrift.length ?? 0) > 0}
    />
  );
}
