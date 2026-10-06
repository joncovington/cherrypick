import type { ContangoMetrics, ContangoPayload, ContangoSessionRow, DatedValue } from "@console/shared";
import { Card, DataCard, PnlCell, fmtNum } from "../../components/DataTable";
import { GridCard, StatTile } from "../../components/grid/GridCard";
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
    <div className="cards cards-wide">
      <Card title="contango" collapseKey="contango-absent">
        <p className="muted">
          This module has not run on this machine -- there is no paper store at{" "}
          <span className="mono">~/.cherrypick/data/contango/paper_trades.db</span> yet. It fills in after its first
          decision window, ten minutes before a session's close.
        </p>
      </Card>
    </div>
  );
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
      lines.push({ label: `${arm}: its rule at 2 bps`, color: "#5b6270", points: rebase(m.expected, base) });
    }
  });
  const firstCapital = Object.values(metrics.arms)[0]?.startingCapital ?? 10_000;
  for (const [symbol, b] of Object.entries(metrics.benchmarks)) {
    lines.push({ label: `buy-and-hold ${symbol}`, color: "#d9a13b", points: rebase(b.series, firstCapital) });
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
    <div className="grid-12">
      <StatTile
        label="VIX/VIX3M today"
        value={today?.usable ? fmtNum(today.ratio, 3) : null}
        tone="dim"
        to="/contango/regime"
        toLabel="the regime series"
        foot={
          today === undefined
            ? "no read yet this session"
            : today.usable
              ? `in below / out at: ${thresholds.join(" · ")}`
              : `refused: ${today.refusal ?? "unusable"}`
        }
      />
      {(data?.arms ?? []).map((a) => (
        <StatTile
          key={a.arm}
          label={a.arm}
          value={a.latestNav === null ? null : fmtMoney(a.latestNav)}
          tone="dim"
          to="/contango/arms"
          toLabel="the arm comparison"
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
      <StatTile
        label="decision window"
        value={windowLabel(data)}
        tone="dim"
        to="/contango/decisions"
        toLabel="the decision journal"
        foot="12:50 on an early close · nothing fills after it"
      />
      <GridCard label="NAV against buy-and-hold" span={6} h={304} to="/contango/performance" foot="each line rebased to the arm's starting capital · after every cost">
        {lines.length === 0 ? (
          <p className="muted">{loading ? "reading…" : "no session decided yet"}</p>
        ) : (
          <TimeLineChart series={lines} height={240} />
        )}
      </GridCard>
      <GridCard label="VIX/VIX3M, recent sessions" span={6} h={304} to="/contango/regime" foot="the thresholds drawn flat · below them is contango">
        <RegimeRatioChart
          rows={(data?.regimeSeries ?? []).slice(-40)}
          thresholds={[...new Set((data?.params.arms ?? []).flatMap((a) => [a.enterBelow, a.exitAtOrAbove]))].map((v) => ({
            label: `threshold ${String(v)}`,
            value: v,
          }))}
          height={240}
        />
      </GridCard>
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
    <div className="cards cards-wide">
      <Card title="VIX/VIX3M, every session" collapseKey="contango-regime-chart">
        <RegimeRatioChart
          rows={data?.regimeSeries ?? []}
          thresholds={[...new Set((data?.params.arms ?? []).flatMap((a) => [a.enterBelow, a.exitAtOrAbove]))].map((v) => ({
            label: `threshold ${String(v)}`,
            value: v,
          }))}
          height={260}
        />
        <p className="integrity-note">
          Read from the stream at the decision tick, not the close the replay used. A stale or missing print is a refusal,
          never the last value carried forward.
        </p>
      </Card>
      <DataCard
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
    </div>
  );
}

export function ContangoArms({ data, metrics }: { data: ContangoPayload | undefined; metrics: ContangoMetrics | undefined }) {
  const armState = new Map((data?.arms ?? []).map((a) => [a.arm, a]));
  const rows = Object.entries(metrics?.arms ?? {});
  const bench = Object.entries(metrics?.benchmarks ?? {});
  return (
    <div className="cards cards-wide">
      <DataCard
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
      <Card title="what the comparison can and cannot say yet" collapseKey="contango-arms-note">
        <p className="integrity-note">
          Replayed from 2018-03 at 2 bps a side, control made 7.4% a year with a 39.8% max drawdown, flipexit 9.8% with
          41.6%, and buy-and-hold SVXY 12.3% with 62.2%: the gate buys drawdown, not return. That is the reference this
          table is read against. Days, not trades: with about 19 switches a year, the sample that matters is sessions,
          and the Sortino and P(Sharpe&nbsp;&gt;&nbsp;0) on the performance page refuse below 14 of them.
        </p>
      </Card>
    </div>
  );
}

export function ContangoPerformance({ data, metrics }: { data: ContangoPayload | undefined; metrics: ContangoMetrics | undefined }) {
  if (metrics !== undefined && !metrics.ok) {
    return (
      <div className="cards cards-wide">
        <Card title="performance" isError>
          <p className="integrity-warn">{metrics.error}</p>
        </Card>
      </div>
    );
  }
  const arms = Object.entries(metrics?.arms ?? {});
  const named: NamedSeries[] = [
    ...arms.map(([arm, m], i) => ({ label: arm, data: m.series, color: SERIES_COLORS[i % SERIES_COLORS.length] })),
    ...Object.entries(metrics?.benchmarks ?? {}).map(([sym, b]) => ({ label: `buy-and-hold ${sym}`, data: b.series as DatedValue[], color: "#d9a13b" })),
  ];
  const windows = [...FIXED_STRESS_WINDOWS, ...inversionWindows(data?.regimeSeries ?? [])];
  return (
    <div className="cards cards-wide">
      <Card title="NAV, its own rule, and buy-and-hold" collapseKey="contango-perf-nav">
        <TimeLineChart series={navLines(metrics, true)} height={260} />
        <p className="integrity-note">
          The grey line per arm is its rule replayed at 2 bps over this module's own recorded ratios and marks: where the
          arm drifts from it, the drift is execution (spread over 2 bps, whole shares, a missed window), not the rule.
        </p>
      </Card>
      <Card title="below the peak" collapseKey="contango-perf-underwater">
        <UnderwaterChart series={named} kind="nav" />
      </Card>
      {arms.map(([arm, m]) => (
        <Card key={arm} title={`${arm} -- tear sheet over ${String(m.reading.days)} days`} collapseKey={`contango-perf-${arm}`}>
          <NavTiles reading={m.reading} />
          <h3 className="muted" style={{ marginTop: 12 }}>
            by month
          </h3>
          <MonthlyHeatmap monthly={m.reading.monthly} kind="nav" />
        </Card>
      ))}
      <Card title="trailing 60-session return" collapseKey="contango-perf-rolling">
        <RollingChart series={named} window={60} kind="nav" />
      </Card>
      <Card title="stress windows" collapseKey="contango-perf-stress">
        <StressTable series={named} windows={windows} kind="nav" />
        <p className="integrity-note">
          The fixed windows predate this module and read "not held" until it has history there; the live rows are every
          stretch its own regime read sat at or above 1.0.
        </p>
      </Card>
    </div>
  );
}

export function ContangoCosts({ data }: { data: ContangoPayload | undefined }) {
  const fills = data?.fills ?? [];
  const floor = data?.params.slippageFloorBps ?? 2;
  const withBps = fills.filter((f) => f.slippageBps !== null);
  const avgBps = withBps.length === 0 ? null : withBps.reduce((t, f) => t + (f.slippageBps ?? 0), 0) / withBps.length;
  const missed = (data?.sessions ?? []).filter((s) => s.action === "missed");
  return (
    <div className="cards cards-wide">
      <div className="grid-12">
        <StatTile label="slippage per fill" value={avgBps === null ? null : `${avgBps.toFixed(2)} bps`} tone="dim" foot={`against the replay's ${String(floor)} bps a side · ${String(withBps.length)} fills`} />
        <StatTile label="fees" value={fmtMoney(fills.reduce((t, f) => t + f.fees, 0))} tone="dim" foot="SEC and TAF on sells; buys are free" />
        <StatTile label="missed windows" value={String(missed.length)} tone="dim" foot="an arm that could not act holds what it held" />
        <StatTile label="distributions" value={fmtMoney((data?.distributions ?? []).reduce((t, d) => t + d.amount, 0))} tone="dim" foot="the cash fund's dividends, credited from the technicals store" />
      </div>
      <DataCard
        title="every fill"
        headers={["session", "arm", "side", "symbol", "shares", "value", "slippage", "bps", "fees"]}
        loading={data === undefined}
        rowCount={fills.length}
        numFrom={4}
        empty="no fill yet"
      >
        {fills.map((f, i) => (
          <tr key={`${f.session}-${f.arm}-${f.side}-${String(i)}`}>
            <td>{f.session}</td>
            <td>{f.arm}</td>
            <td>{f.side}</td>
            <td>{f.symbol}</td>
            <td>{f.shares}</td>
            <td>{fmtCash(f.side === "buy" ? -f.value : f.value)}</td>
            <td>{fmtMoney(-f.slippage)}</td>
            <td className={f.slippageBps !== null && f.slippageBps > floor + 0.01 ? "pnl-neg" : undefined}>
              {f.slippageBps === null ? "—" : f.slippageBps.toFixed(2)}
            </td>
            <td>{fmtMoney(-f.fees)}</td>
          </tr>
        ))}
      </DataCard>
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
        headers={["ex-date", "arm", "symbol", "per share", "shares", "amount", "credited"]}
        loading={data === undefined}
        rowCount={data?.distributions.length ?? 0}
        numFrom={3}
        empty="none yet -- SHV pays monthly, credited when the technicals store carries the row"
      >
        {(data?.distributions ?? []).map((d) => (
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
    </div>
  );
}

export function ContangoPositions({ data }: { data: ContangoPayload | undefined }) {
  const open = (data?.stints ?? []).filter((s) => s.status === "open");
  return (
    <div className="cards cards-wide">
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
    </div>
  );
}

export function ContangoHistory({ data }: { data: ContangoPayload | undefined }) {
  const closed = (data?.stints ?? []).filter((s) => s.status === "closed");
  const sessions = data?.sessions ?? [];
  return (
    <div className="cards cards-wide">
      <DataCard
        title="closed holding stints"
        headers={["held", "arm", "symbol", "shares", "entry", "exit", "distrib.", "gross", "fees", "slip", "net", "why it ended"]}
        loading={data === undefined}
        rowCount={closed.length}
        numFrom={3}
        empty="no stint has closed yet -- one closes on each switch"
      >
        {closed.map((s) => (
          <tr key={s.positionId}>
            <td>
              {s.entrySession}
              <span className="muted"> → {s.exitSession ?? "—"}</span>
            </td>
            <td>{s.arm}</td>
            <td>{s.symbol}</td>
            <td>{s.shares}</td>
            <td>{fmtCash(s.entryValue)}</td>
            <td>{fmtCash(s.exitValue)}</td>
            <td>{fmtCash(s.distributions)}</td>
            <td>
              <PnlCell v={s.grossPnl} />
            </td>
            <td>{fmtMoney(s.fees === null ? null : -s.fees)}</td>
            <td>{fmtMoney(s.slippage === null ? null : -s.slippage)}</td>
            <td>
              <PnlCell v={s.netPnl} />
            </td>
            <td className="muted">{s.exitReason ?? "—"}</td>
          </tr>
        ))}
      </DataCard>
      <DataCard
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
      <MeasurementBreaks breaks={data?.measurementBreaks ?? []} />
    </div>
  );
}

export function ContangoDecisions() {
  return (
    <div className="cards cards-wide">
      <DecisionsCard module="contango" />
    </div>
  );
}

export function ContangoHelp({ data }: { data: ContangoPayload | undefined }) {
  const arms = data?.params.arms ?? [];
  return (
    <div className="cards cards-wide">
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
    </div>
  );
}
