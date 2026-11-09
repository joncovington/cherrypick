import { useState } from "react";
import type { ContangoHistoryRow, ContangoMetrics, ContangoPayload, ContangoSessionRow, DatedValue } from "@console/shared";
import { Card, DataCard, PnlCell, fmtNum, sortRows, useSort } from "../../components/DataTable";
import { ArmSelect } from "../../components/ArmSelect";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { useContangoHistory } from "../../lib/api";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { TabLink } from "../Flies/FliesFilter";
import { TimeLineChart, type TimeLineSeries } from "../../components/chart/TimeLineChart";
import { SERIES_COLORS } from "../../components/Charts";
import { DecisionsCard } from "../../components/DecisionsCard";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";
import {
  MonthlyHeatmap,
  NavTiles,
  RegimeRatioChart,
  RollingChart,
  StressTable,
  UnderwaterChart,
  type NamedSeries,
} from "../../components/nav/NavViews";
import { FIXED_STRESS_WINDOWS, inversionWindows, rebase } from "../../lib/navSeries";
import { fmtCash, fmtMoney } from "../../lib/format";
import { GuideLink } from "../../components/GuideLink";

/**
 * contango's pages (packages/contango): the VIX/VIX3M switch held in shares. The judged series is
 * each arm's daily NAV; trades are holding stints and there are few of them. Everything here is
 * the module's own ledger (`/api/contango`) or its own analytics (`/api/contango/metrics`, i.e.
 * `core.metrics.nav`); the page derives display transforms and nothing else.
 *
 * Tones are signs, never verdicts. A metric the reading refused (too few days) is "—", not 0.
 */

const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "—" : `${v < 0 ? "-" : ""}${Math.abs(v * 100).toFixed(digits)}%`;

function absentCard() {
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="not started" question="Has this module run on this machine?">
        <Card title="contango" collapseKey="contango-absent">
          <p className="muted">
            This module has not run on this machine -- there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/contango/paper_trades.db</span> yet. It fills in after its first
            decision window, ten minutes before a session's close.
          </p>
        </Card>
      </Section>
    </div>
  );
}

/** A card's ? holding one explanatory paragraph: the caption that used to sit under the figure. */
function oneNote(title: string, heading: string, text: string): CardHelpContent {
  return {
    title,
    body: (
      <HelpSection title={heading}>
        <p>{text}</p>
      </HelpSection>
    ),
  };
}

function windowLabel(data: ContangoPayload | undefined): string {
  if (data === undefined) return "—";
  const before = data.params.decisionMinutesBeforeClose;
  const start = 16 * 60 - before;
  const end = start + data.params.decisionWindowMinutes;
  const hm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return `${hm(start)}–${hm(end)} ET`;
}

/** Each arm's NAV and buy-and-hold, rebased to a common start so the lines share an axis. */
function navLines(metrics: ContangoMetrics | undefined, withExpected: boolean): TimeLineSeries[] {
  if (metrics === undefined) return [];
  const lines: TimeLineSeries[] = [];
  Object.entries(metrics.arms).forEach(([arm, m], i) => {
    const base = m.startingCapital ?? 10_000;
    lines.push({ label: arm, color: SERIES_COLORS[i % SERIES_COLORS.length], points: rebase(m.series, base) });
    if (withExpected && m.expected.length > 1) {
      lines.push({ label: `${arm}: its rule at 2 bps`, color: "var(--text-dim)", points: rebase(m.expected, base) });
    }
  });
  const firstCapital = Object.values(metrics.arms)[0]?.startingCapital ?? 10_000;
  for (const [symbol, b] of Object.entries(metrics.benchmarks)) {
    lines.push({ label: `buy-and-hold ${symbol}`, color: "var(--amber-line)", points: rebase(b.series, firstCapital) });
  }
  return lines.filter((l) => l.points.length > 0);
}

export function ContangoToday({
  data,
  metrics,
  loading,
}: {
  data: ContangoPayload | undefined;
  metrics: ContangoMetrics | undefined;
  loading: boolean;
}) {
  if (data !== undefined && !data.dbPresent) return absentCard();
  const today = data?.regimeSeries.find((r) => r.tradeDate === data.session);
  const thresholds = (data?.params.arms ?? []).map((a) => `${a.arm} ${String(a.enterBelow)}/${String(a.exitAtOrAbove)}`);
  const lines = navLines(metrics, false);
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the day" question="Where does each arm stand today?">
      <FliesTile
        title="VIX/VIX3M today"
        value={today?.usable ? fmtNum(today.ratio, 3) : null}
        tone="dim"
        to="/contango/regime"
        toLabel="regime"
        foot={
          today === undefined
            ? "no read yet this session"
            : today.usable
              ? `in below / out at: ${thresholds.join(" · ")}`
              : `refused: ${today.refusal ?? "unusable"}`
        }
      />
      {(data?.arms ?? []).map((a) => (
        <FliesTile
          key={a.arm}
          title={a.arm}
          value={a.latestNav === null ? null : fmtMoney(a.latestNav)}
          tone="dim"
          to="/contango/arms"
          toLabel="arms"
          foot={
            <>
              {a.holding === null ? "holding nothing yet" : `${a.holding.symbol} ${String(a.holding.shares)} sh (${a.holding.role})`}
              {" · today: "}
              {a.today?.action ?? "waiting for the window"}
              {a.today?.refusal ? ` (${a.today.refusal})` : ""}
            </>
          }
        />
      ))}
      <FliesTile
        title="decision window"
        value={windowLabel(data)}
        tone="dim"
        to="/contango/decisions"
        toLabel="decisions"
        foot="12:50 on an early close · nothing fills after it"
      />
      </Section>

      <Section concept="hist" tag="the record" question="How have NAV and the regime moved lately?">
      <Card
        title="NAV against buy-and-hold"
        className="fl-w6"
        help={oneNote("NAV against buy-and-hold", "What is drawn", "Each line rebased to the arm's starting capital, after every cost.")}
      >
        {lines.length === 0 ? (
          <p className="muted">{loading ? "reading…" : "no session decided yet"}</p>
        ) : (
          <TimeLineChart series={lines} height={240} />
        )}
        <div className="pf-mores pf-push">
          <TabLink to="/contango/performance">performance</TabLink>
        </div>
      </Card>
      <Card
        title="VIX/VIX3M, recent sessions"
        className="fl-w6"
        help={oneNote("VIX/VIX3M, recent sessions", "Reading the chart", "The thresholds are drawn flat; below them is contango.")}
      >
        <RegimeRatioChart
          rows={(data?.regimeSeries ?? []).slice(-40)}
          thresholds={[...new Set((data?.params.arms ?? []).flatMap((a) => [a.enterBelow, a.exitAtOrAbove]))].map((v) => ({
            label: `threshold ${String(v)}`,
            value: v,
          }))}
          height={240}
        />
        <div className="pf-mores pf-push">
          <TabLink to="/contango/regime">regime</TabLink>
        </div>
      </Card>
      </Section>
    </div>
  );
}

export function ContangoRegime({ data }: { data: ContangoPayload | undefined }) {
  const arms = data?.params.arms.map((a) => a.arm) ?? [];
  const byDay = new Map<string, Map<string, ContangoSessionRow>>();
  for (const s of data?.sessions ?? []) {
    const m = byDay.get(s.tradeDate) ?? new Map<string, ContangoSessionRow>();
    m.set(s.arm, s);
    byDay.set(s.tradeDate, m);
  }
  const rows = [...(data?.regimeSeries ?? [])].reverse().slice(0, 120);
  return (
    <div className="pf-layout">
      <Section concept="dir" tag="the regime" question="What has the VIX/VIX3M ratio done, session by session?">
      <Card
        title="VIX/VIX3M, every session"
        collapseKey="contango-regime-chart"
        help={oneNote(
          "VIX/VIX3M, every session",
          "Where the read comes from",
          "Read from the stream at the decision tick, not the close the replay used. A stale or missing print is a refusal, never the last value carried forward.",
        )}
      >
        <RegimeRatioChart
          rows={data?.regimeSeries ?? []}
          thresholds={[...new Set((data?.params.arms ?? []).flatMap((a) => [a.enterBelow, a.exitAtOrAbove]))].map((v) => ({
            label: `threshold ${String(v)}`,
            value: v,
          }))}
          height={260}
        />
      </Card>
      </Section>

      <Section concept="room" tag="the decisions" question="What did each arm do on each reading?">
      <DataCard
        className="lv-12"
        title="the reading and what each arm did"
        headers={["date", "ratio", "VIX", "VIX3M", ...arms]}
        loading={data === undefined}
        rowCount={rows.length}
        numFrom={1}
        empty="no regime read recorded yet"
      >
        {rows.map((r) => (
          <tr key={r.tradeDate}>
            <td>{r.tradeDate}</td>
            <td>{r.usable ? fmtNum(r.ratio, 3) : <span className="chip chip-warn integrity-chip">{r.refusal ?? "unusable"}</span>}</td>
            <td>{fmtNum(r.vix, 2)}</td>
            <td>{fmtNum(r.vix3m, 2)}</td>
            {arms.map((a) => {
              const s = byDay.get(r.tradeDate)?.get(a);
              return (
                <td key={a} title={s?.refusal ?? undefined}>
                  {s === undefined ? "—" : `${s.action ?? "—"} → ${s.stateAfter ?? "—"}`}
                </td>
              );
            })}
          </tr>
        ))}
      </DataCard>
      </Section>
    </div>
  );
}

export function ContangoArms({ data, metrics }: { data: ContangoPayload | undefined; metrics: ContangoMetrics | undefined }) {
  const armState = new Map((data?.arms ?? []).map((a) => [a.arm, a]));
  const rows = Object.entries(metrics?.arms ?? {});
  const bench = Object.entries(metrics?.benchmarks ?? {});
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the arms" question="How does each arm compare with buy-and-hold?">
      <DataCard
        className="lv-12"
        help={ARMS_HELP}
        flag="days, not trades"
        title="arms against buy-and-hold, on daily NAV"
        headers={["series", "days", "CAGR", "return", "max DD", "MAR", "Sortino", "worst day", "switches", "missed", "distributions", "vs its rule"]}
        loading={metrics === undefined}
        isError={metrics !== undefined && !metrics.ok}
        rowCount={rows.length + bench.length}
        numFrom={1}
        empty={metrics?.error ?? "no session decided yet"}
      >
        {rows.map(([arm, m]) => {
          const s = armState.get(arm);
          return (
            <tr key={arm}>
              <td>{arm}</td>
              <td>{m.reading.days}</td>
              <td>{pct(m.reading.cagr)}</td>
              <td>{pct(m.reading.totalReturn)}</td>
              <td>{pct(m.reading.maxDrawdown)}</td>
              <td>{fmtNum(m.reading.mar ?? null, 2)}</td>
              <td>{fmtNum(m.reading.sortino ?? null, 2)}</td>
              <td>{pct(m.reading.worstDay, 2)}</td>
              <td>{s?.switches ?? "—"}</td>
              <td>{s?.missed ?? "—"}</td>
              <td>{s === undefined ? "—" : fmtMoney(s.distributionsTotal)}</td>
              <td title="the arm's NAV against its own rule replayed at 2 bps over the same ratios and marks: execution, not the rule">
                {pct(m.tracking, 2)}
              </td>
            </tr>
          );
        })}
        {bench.map(([symbol, b]) => (
          <tr key={symbol} className="muted">
            <td>buy-and-hold {symbol}</td>
            <td>{b.reading.days}</td>
            <td>{pct(b.reading.cagr)}</td>
            <td>{pct(b.reading.totalReturn)}</td>
            <td>{pct(b.reading.maxDrawdown)}</td>
            <td>{fmtNum(b.reading.mar ?? null, 2)}</td>
            <td>{fmtNum(b.reading.sortino ?? null, 2)}</td>
            <td>{pct(b.reading.worstDay, 2)}</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
          </tr>
        ))}
      </DataCard>
      </Section>
    </div>
  );
}

const ARMS_HELP: CardHelpContent = {
  title: "What the comparison can and cannot say yet",
  body: (
    <>
      <HelpWarn title="Days, not trades">
        <p>
          With about 19 switches a year, the sample that matters is sessions, and the Sortino and P(Sharpe&nbsp;&gt;&nbsp;0) on the
          performance page refuse below 14 of them.
        </p>
      </HelpWarn>
      <HelpSection title="The reference this table is read against">
        <p>
          Replayed from 2018-03 at 2 bps a side, control made 7.4% a year with a 39.8% max drawdown, flipexit 9.8% with 41.6%, and
          buy-and-hold SVXY 12.3% with 62.2%: the gate buys drawdown, not return.
        </p>
      </HelpSection>
    </>
  ),
};

export function ContangoPerformance({ data, metrics }: { data: ContangoPayload | undefined; metrics: ContangoMetrics | undefined }) {
  if (metrics !== undefined && !metrics.ok) {
    return (
      <div className="pf-layout">
        <Section concept="stand" tag="the reading" question="Why is there no NAV reading?">
          <Card title="performance" isError>
            <p className="integrity-warn">{metrics.error}</p>
          </Card>
        </Section>
        <PerformanceSlide module="contango" />
      </div>
    );
  }
  const arms = Object.entries(metrics?.arms ?? {});
  const named: NamedSeries[] = [
    ...arms.map(([arm, m], i) => ({ label: arm, data: m.series, color: SERIES_COLORS[i % SERIES_COLORS.length] })),
    ...Object.entries(metrics?.benchmarks ?? {}).map(([sym, b]) => ({ label: `buy-and-hold ${sym}`, data: b.series as DatedValue[], color: "var(--amber-line)" })),
  ];
  const windows = [...FIXED_STRESS_WINDOWS, ...inversionWindows(data?.regimeSeries ?? [])];
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="NAV" question="What is each arm worth, against its own rule and buy-and-hold?">
      <Card
        title="NAV, its own rule, and buy-and-hold"
        collapseKey="contango-perf-nav"
        className="lv-12"
        help={oneNote(
          "NAV, its own rule, and buy-and-hold",
          "The grey line",
          "The grey line per arm is its rule replayed at 2 bps over this module's own recorded ratios and marks: where the arm drifts from it, the drift is execution (spread over 2 bps, whole shares, a missed window), not the rule.",
        )}
      >
        <TimeLineChart series={navLines(metrics, true)} height={260} />
      </Card>
      <Card title="below the peak" collapseKey="contango-perf-underwater" className="lv-12">
        <UnderwaterChart series={named} kind="nav" />
      </Card>
      </Section>

      <Section concept="hist" tag="tear sheets" question="How did each arm do over its days, month by month?">
      {arms.map(([arm, m]) => (
        <Card key={arm} title={`${arm} -- tear sheet over ${String(m.reading.days)} days`} collapseKey={`contango-perf-${arm}`} className="lv-12">
          <NavTiles reading={m.reading} />
          <h3 className="muted" style={{ marginTop: 12 }}>
            by month
          </h3>
          <MonthlyHeatmap monthly={m.reading.monthly} kind="nav" />
        </Card>
      ))}
      </Section>

      <Section concept="risk" tag="stress" question="How did NAV hold up when the regime inverted?">
      <Card title="trailing 60-session return" collapseKey="contango-perf-rolling" className="lv-12">
        <RollingChart series={named} window={60} kind="nav" />
      </Card>
      <Card
        title="stress windows"
        collapseKey="contango-perf-stress"
        className="lv-12"
        help={oneNote(
          "Stress windows",
          "Fixed and live windows",
          "The fixed windows predate this module and read \"not held\" until it has history there; the live rows are every stretch its own regime read sat at or above 1.0.",
        )}
      >
        <StressTable series={named} windows={windows} kind="nav" />
      </Card>
      </Section>
      <PerformanceSlide module="contango" />
    </div>
  );
}

export function ContangoCosts({ data }: { data: ContangoPayload | undefined }) {
  const fills = data?.fills ?? [];
  const fillSort = useSort();
  const distSort = useSort();
  // Costs are shown as money out, and sort as shown.
  const sortedFills = sortRows(fills, fillSort, {
    session: (f) => f.session,
    arm: (f) => f.arm,
    side: (f) => f.side,
    symbol: (f) => f.symbol,
    shares: (f) => f.shares,
    value: (f) => f.value,
    slippage: (f) => (f.slippage === null ? null : -f.slippage),
    bps: (f) => f.slippageBps,
    fees: (f) => (f.fees === null ? null : -f.fees),
  });
  const distributions = sortRows(data?.distributions ?? [], distSort, {
    exDate: (d) => d.exDate,
    arm: (d) => d.arm,
    symbol: (d) => d.symbol,
    perShare: (d) => d.perShare,
    shares: (d) => d.shares,
    amount: (d) => d.amount,
    credited: (d) => d.creditedSession,
  });
  const floor = data?.params.slippageFloorBps ?? 2;
  const withBps = fills.filter((f) => f.slippageBps !== null);
  const withFees = fills.filter((f) => f.fees !== null);
  const avgBps = withBps.length === 0 ? null : withBps.reduce((t, f) => t + (f.slippageBps ?? 0), 0) / withBps.length;
  const missed = (data?.sessions ?? []).filter((s) => s.action === "missed");
  return (
    <div className="pf-layout">
      <Section concept="risk" tag="costs" question="What did trading cost, and what did the cash fund pay?">
        <FliesTile title="slippage per fill" value={avgBps === null ? null : `${avgBps.toFixed(2)} bps`} tone="dim" foot={`against the replay's ${String(floor)} bps a side · ${String(withBps.length)} fills`} />
        <FliesTile
          title="fees"
          value={withFees.length === 0 ? null : fmtMoney(withFees.reduce((t, f) => t + (f.fees ?? 0), 0))}
          tone="dim"
          foot={`SEC and TAF on sells; buys are free${withFees.length < fills.length ? ` · ${String(withFees.length)} of ${String(fills.length)} fills recorded` : ""}`}
        />
        <FliesTile title="missed windows" value={String(missed.length)} tone="dim" foot="an arm that could not act holds what it held" />
        <FliesTile title="distributions" value={fmtMoney((data?.distributions ?? []).reduce((t, d) => t + d.amount, 0))} tone="dim" foot="the cash fund's dividends, credited from the technicals store" />
      </Section>
      <Section concept="hist" tag="fills" question="What did each fill cost?">
      <DataCard
        className="lv-12"
        title="every fill"
        sort={fillSort}
        headers={[
          { label: "session", k: "session" },
          { label: "arm", k: "arm" },
          { label: "side", k: "side" },
          { label: "symbol", k: "symbol" },
          { label: "shares", k: "shares" },
          { label: "value", k: "value" },
          { label: "slippage", k: "slippage" },
          { label: "bps", k: "bps" },
          { label: "fees", k: "fees" },
        ]}
        loading={data === undefined}
        rowCount={fills.length}
        numFrom={4}
        empty="no fill yet"
      >
        {sortedFills.map((f, i) => (
          <tr key={`${f.session}-${f.arm}-${f.side}-${String(i)}`}>
            <td>{f.session}</td>
            <td>{f.arm}</td>
            <td>{f.side}</td>
            <td>{f.symbol}</td>
            <td>{f.shares}</td>
            <td>{fmtCash(f.value)}</td>
            <td>{f.slippage === null ? "n/r" : fmtMoney(-f.slippage)}</td>
            <td className={f.slippageBps !== null && f.slippageBps > floor + 0.01 ? "pnl-neg" : undefined}>
              {f.slippageBps === null ? "—" : f.slippageBps.toFixed(2)}
            </td>
            <td>{f.fees === null ? "n/r" : fmtMoney(-f.fees)}</td>
          </tr>
        ))}
      </DataCard>
      </Section>
      <Section concept="room" tag="windows and cash" question="Which windows were missed, and what was credited?">
      <DataCard
        title="missed decision windows"
        headers={["session", "arm", "why", "holding"]}
        loading={data === undefined}
        rowCount={missed.length}
        empty="none missed"
      >
        {missed.map((s) => (
          <tr key={`${s.tradeDate}-${s.arm}`}>
            <td>{s.tradeDate}</td>
            <td>{s.arm}</td>
            <td>{s.refusal ?? "—"}</td>
            <td>{s.holdingSymbol ?? "nothing"}</td>
          </tr>
        ))}
      </DataCard>
      <DataCard
        title="distributions credited"
        sort={distSort}
        headers={[
          { label: "ex-date", k: "exDate" },
          { label: "arm", k: "arm" },
          { label: "symbol", k: "symbol" },
          { label: "per share", k: "perShare" },
          { label: "shares", k: "shares" },
          { label: "amount", k: "amount" },
          { label: "credited", k: "credited" },
        ]}
        loading={data === undefined}
        rowCount={distributions.length}
        numFrom={3}
        empty="none yet -- SHV pays monthly, credited when the technicals store carries the row"
      >
        {distributions.map((d) => (
          <tr key={`${d.arm}-${d.exDate}-${d.symbol}`}>
            <td>{d.exDate}</td>
            <td>{d.arm}</td>
            <td>{d.symbol}</td>
            <td>{d.perShare.toFixed(4)}</td>
            <td>{d.shares}</td>
            <td>{fmtCash(d.amount)}</td>
            <td>{d.creditedSession}</td>
          </tr>
        ))}
      </DataCard>
      </Section>
    </div>
  );
}

export function ContangoPositions({ data }: { data: ContangoPayload | undefined }) {
  const open = (data?.stints ?? []).filter((s) => s.status === "open");
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the book" question="What does each arm hold now?">
      <DataCard
        title="what each arm holds"
        headers={["arm", "symbol", "role", "since", "shares", "entry price", "entry", "mark", "unrealised net"]}
        loading={data === undefined}
        rowCount={open.length}
        numFrom={4}
        empty="nothing held yet -- the first decision buys"
      >
        {open.map((s) => (
          <tr key={s.positionId}>
            <td>{s.arm}</td>
            <td>{s.symbol}</td>
            <td>{s.role}</td>
            <td>{s.entrySession}</td>
            <td>{s.shares}</td>
            <td>{fmtNum(s.entryMid, 2)}</td>
            <td>{fmtCash(s.entryValue)}</td>
            <td>{fmtNum(s.mark, 2)}</td>
            <td title="at the latest recorded mark, less the entry's fee and slippage, plus distributions">
              <PnlCell v={s.unrealisedNet} />
            </td>
          </tr>
        ))}
      </DataCard>
      </Section>
    </div>
  );
}

const MONEY = tradeMoneyColumns<ContangoHistoryRow>("the entry mid per share: a purchase, so a debit");

/** The standard's columns, with the fund's distributions as a third cash flow between exit and
 *  gross: entry + exit + distributions = gross. */
const HISTORY_COLUMNS: ColumnDef<ContangoHistoryRow>[] = [
  {
    id: "dates",
    header: "held",
    kind: "describe",
    render: (r) => (
      <>
        {r.entrySession}
        <span className="muted"> → {r.exitSession ?? "—"}</span>
      </>
    ),
    sortValue: (r) => r.entrySession,
  },
  { id: "arm", header: "arm", kind: "describe", render: (r) => r.arm, sortValue: (r) => r.arm },
  { id: "symbol", header: "symbol", kind: "describe", render: (r) => r.symbol, sortValue: (r) => r.symbol },
  ...MONEY.describe,
  { id: "reason", header: "why it ended", kind: "describe", className: "muted", render: (r) => r.exitReason ?? "—" },
  ...MONEY.money.flatMap((c): ColumnDef<ContangoHistoryRow>[] => {
    if (c.id === "gross") return [{ ...c, title: "entry + exit + distributions, before any cost" }];
    if (c.id === "settle") return [{ ...c, title: "a share trade records no settlement charge" }];
    if (c.id !== "exit") return [c];
    return [
      { ...c, title: "the sale, whole position" },
      {
        id: "distrib",
        header: "distrib.",
        title: "the fund's distributions credited while it was held",
        kind: "money",
        render: (r) => fmtCash(r.distributions),
        sortValue: (r) => r.distributions,
      },
    ];
  }),
];

function ContangoStintHistory({ data }: { data: ContangoPayload | undefined }) {
  const [arm, setArm] = useState<string | null>(null);
  const { from, to } = useUrlDateRange();
  const history = useContangoHistory({ arm, from, to });
  return (
    <HistoryTable
      table="contango-history"
      title="closed holding stints"
      className="view-fade"
      defs={HISTORY_COLUMNS}
      clientSort
      rows={history.data?.rows ?? []}
      rowKey={(r) => r.positionId}
      loading={history.isLoading}
      isError={history.isError}
      busy={history.isPlaceholderData}
      empty={
        from !== null || to !== null
          ? "no stint closed in this date range"
          : "no stint has closed yet -- one closes on each switch"
      }
      updatedAt={history.data === undefined ? undefined : history.dataUpdatedAt}
      dateBasis="closed"
      filters={
        <ArmSelect module="contango" value={arm} options={data?.params.arms.map((a) => a.arm)} onChange={setArm} />
      }
      footer={history.data !== undefined && <TradeTotalsChip totals={history.data.totals} noun="stints" settlementRecorded={false} />}
    />
  );
}

export function ContangoHistory({ data }: { data: ContangoPayload | undefined }) {
  const sessions = data?.sessions ?? [];
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the record" question="How did each holding stint end?">
        <ContangoStintHistory data={data} />
      </Section>
      <Section concept="edge" tag="daily" question="What did each arm decide, and what was it worth, each day?">
      <DataCard
        className="lv-12"
        title="daily NAV log"
        headers={["date", "arm", "ratio", "before → after", "action", "holding", "cash", "NAV"]}
        loading={data === undefined}
        rowCount={sessions.length}
        numFrom={2}
        empty="no session decided yet"
      >
        {sessions.slice(0, 200).map((s) => (
          <tr key={`${s.tradeDate}-${s.arm}`}>
            <td>{s.tradeDate}</td>
            <td>{s.arm}</td>
            <td>{fmtNum(s.ratio, 3)}</td>
            <td>
              {s.stateBefore ?? "—"} → {s.stateAfter ?? "—"}
            </td>
            <td title={s.refusal ?? undefined}>{s.action ?? "—"}</td>
            <td>{s.holdingSymbol === null ? "—" : `${s.holdingSymbol} ${String(s.shares ?? "")}`}</td>
            <td>{fmtMoney(s.cash)}</td>
            <td>{fmtMoney(s.nav)}</td>
          </tr>
        ))}
      </DataCard>
      </Section>
      <MeasurementBreaks breaks={data?.measurementBreaks ?? []} />
    </div>
  );
}

export function ContangoDecisions() {
  return (
    <div className="pf-layout">
      <Section concept="add" tag="decisions" question="What did each arm decide, and why?">
        <DecisionsCard module="contango" />
      </Section>
    </div>
  );
}

export function ContangoHelp({ data }: { data: ContangoPayload | undefined }) {
  const arms = data?.params.arms ?? [];
  return (
    <div className="pf-layout">
      <GuideLink to="/help/strategy-pages#contango-contango" />
      <Section concept="stand" tag="the idea" question="What is contango?">
      <Card title="what contango is" collapseKey="contango-help-what">
        <div className="pmcc-prose">
          <p>
            The VIX/VIX3M switch held in shares: each arm holds SVXY (-0.5x short VIX futures) while the ratio says
            contango, and SHV (T-bills) while it does not, deciding once a session ten minutes before the close. It is the
            scalable expression of the signal curve trades in VXX options, where costs take about half of each credit.
            Paper only; there is no order path anywhere in the module.
          </p>
        </div>
      </Card>
      </Section>
      <Section concept="dir" tag="the arms" question="What does each arm hold, and when?">
      <Card title="the arms -- two thresholds each" collapseKey="contango-help-arms">
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            {arms.map((a) => (
              <div key={a.arm}>
                <dt>{a.arm}</dt>
                <dd>
                  Holds {a.riskSymbol} below {a.enterBelow}, {a.cashSymbol} at or above {a.exitAtOrAbove}
                  {a.exitAtOrAbove > a.enterBelow ? ", and keeps whatever it holds in between" : ""}. Opened with{" "}
                  {fmtMoney(a.startingCapital)}.
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </Card>
      </Section>
      <Section concept="edge" tag="the rules" question="What keeps the results honest?">
      <Card title="the honesty rules" collapseKey="contango-help-rules">
        <div className="pmcc-prose">
          <ol className="pmcc-rules">
            <li>One decision per arm per session, inside the window or not at all. An arm that cannot act is recorded missed and keeps its holding; nothing fills after the window.</li>
            <li>A switch checks both funds' quotes before planning either, and writes the closed stint, the new one and the cash together.</li>
            <li>Fills at mid; slippage is its own cost, half the spread or {String(data?.params.slippageFloorBps ?? 2)} bps of mid, whichever is larger.</li>
            <li>The cash fund's distributions are credited from the local technicals store to a stint that held at the ex-date open.</li>
            <li>A stale VIX or VIX3M print refuses; it is never carried forward.</li>
          </ol>
        </div>
      </Card>
      </Section>
    </div>
  );
}
