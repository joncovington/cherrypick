import { useQuery } from "@tanstack/react-query";
import type { LiveFliesPayload, LiveOnRisk, LivePerformance, LivePeriod, LivePosition } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { MetricTiles } from "../../components/performance/MetricTiles";
import { TileGrid } from "../../components/performance/TileGrid";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { fmtMoney, fmtPrice } from "../../lib/format";
import {
  CompletionCard,
  CompletionTrend,
  LiveVsPaperCard,
  Tile as PerfTile,
  fmtRatio,
  tone as perfTone,
} from "../Flies/PerformanceTab";
import { Pager, usePage } from "../../components/ScopeBar";
import { LiveChart } from "./LiveChart";
import { LiveBoxes, StateLine } from "./liveParts";
import type { Box } from "./PortfolioBox";
import { Section } from "../../components/Section";
import { structureLegs } from "../Flies/structure";
import { BoxButton, BoxButtons } from "./portfolioBoxContext";
import { TradeCards, type TradeCardModel } from "./TradeCard";

const ACTIVITY_PAGE_SIZES = [10, 25, 50, 100] as const;

/**
 * The flies live pilot's day, read-only (2026-09-17). Two honesty rules the page states on its
 * face: the period tiles are SETTLED net only (flies settles at expiry, so today reads zero until
 * the bell, and the intraday number beside it is the mark), and a mark is a MID, not a fill. There
 * is no button here that touches an order: the desk CLI is the suite's only discretionary path,
 * and this console holds no order code by design.
 */
export function useLiveFlies() {
  return useQuery<LiveFliesPayload>({
    queryKey: ["live-flies"],
    queryFn: async () => {
      const res = await fetch("/api/live/flies");
      if (!res.ok) throw new Error(`live: HTTP ${res.status}`);
      return (await res.json()) as LiveFliesPayload;
    },
    refetchInterval: 15_000,
  });
}

function signed(v: number | null): string {
  if (v === null) return "—";
  return fmtMoney(v);
}

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined) return "muted";
  return v >= 0 ? "pnl-pos" : "pnl-neg";
}

/** A signed return, e.g. `+32.8%`; an em-dash where it is undefined, never 0. */
function fmtReturn(r: number | null): string {
  if (r === null) return "—";
  return `${r >= 0 ? "+" : ""}${(r * 100).toFixed(1)}%`;
}

function onRiskTitle(o: LiveOnRisk): string {
  return (
    `settled net ${fmtMoney(o.net)} over the sum of each session's peak worst-case exposure ${fmtMoney(o.peakRisk)}, ` +
    `${o.sessions} of ${o.of} session${o.of === 1 ? "" : "s"} (a session counts once it is finished and recorded a peak; ` +
    `before 2026-09-17 none did)`
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "now" only while the page reads today's running session; any other session is named, or a
 *  retired module's last mark from days ago reads as live. */
export function markTileLabel(d: Pick<LiveFliesPayload, "session" | "sessionBasis">): string {
  return d.sessionBasis === "today" ? "now · mark (mid)" : `${d.session} · last mark (mid)`;
}

/** The paper side is the control arm over the sessions the live figure covers, never the calendar
 *  period -- the paper loop ran sessions the pilot sat out. */
export function periodTitle(p: LivePeriod, arm: string | null): string {
  const live = `live settled net, ${arm !== null ? `the ${arm} arm only` : "every arm"}: ${plural(p.sessions, "session")}, ${plural(p.trades, "structure")}`;
  const paper =
    p.paperNet === null
      ? "paper control: n/a"
      : `paper control over the same ${plural(p.sessions, "session")} live traded: ${fmtMoney(p.paperNet)} (it settled on ${p.paperSessions ?? 0} of them)`;
  return `${live}\n${paper}\n\n${onRiskTitle(p.onRisk)}`;
}

function PeriodTile({ label, p, arm }: { label: string; p: LivePeriod; arm: string | null }) {
  return (
    <div className="stat-tile" title={periodTitle(p, arm)}>
      <span className="stat-label">{label} · settled</span>
      <span className={`stat-value ${tone(p.trades > 0 ? p.net : null)}`}>{p.trades > 0 ? fmtMoney(p.net) : "—"}</span>
      <span className="muted" style={{ display: "block", fontSize: 11 }}>
        {p.trades} live{arm !== null ? ` ${arm}` : ""} · {plural(p.sessions, "session")} · paper{" "}
        {p.paperNet === null || p.sessions === 0 ? "—" : `${fmtMoney(p.paperNet)} same sessions`}
      </span>
      <span className="muted" style={{ display: "block", fontSize: 11 }}>
        <span className={tone(p.onRisk.ratio)}>{fmtReturn(p.onRisk.ratio)}</span> on session peak risk
        {p.onRisk.ratio !== null && p.onRisk.sessions < p.onRisk.of ? ` (${p.onRisk.sessions}/${p.onRisk.of})` : ""}
      </span>
    </div>
  );
}

const PERF_HELP: CardHelpContent = {
  title: "Performance",
  body: (
    <>
      <HelpWarn title="Few sessions">
        <p>
          Ratios are annualised on 252 sessions from however many the pilot has traded: a ratio over that few sessions describes this stretch, not the strategy. A Sharpe above 3 on a
          sample this small is a warning about the sample.
        </p>
      </HelpWarn>
      <HelpSection title="1-lot samples">
        <p>The curve is cumulative net P&amp;L and drawdown of 1-lot samples, not a sized book.</p>
      </HelpSection>
      <HelpSection title="Flies has no return on capital">
        <p>
          The ledger carries no per-trade capital or ceiling, because a legged book&apos;s risk depends on completion. Return on session peak risk takes return on capital&apos;s place; capture
          rate reads —.
        </p>
      </HelpSection>
    </>
  ),
};

/** The flies study tabs' live figures for the pilot's arm, plus return on session peak risk. */
function PerformanceCard({ perf }: { perf: LivePerformance }) {
  const t = perf.tiles;
  const r = perf.risk;
  return (
    <Card title={`performance · ${perf.arm ?? "every arm"} · every live session`} collapseKey="live-flies-performance" className="lv-12" help={PERF_HELP} flag="few sessions">
      <TileGrid count={12}>
        <PerfTile label="return on session peak risk" value={fmtReturn(perf.onRisk.ratio)} tone={perfTone(perf.onRisk.ratio)} title={onRiskTitle(perf.onRisk)} />
        <PerfTile label="net P&L" value={fmtMoney(t.netPnl)} tone={perfTone(t.netPnl)} />
        <PerfTile label="trades · sessions" value={`${t.trades} · ${t.sessions}`} />
        <PerfTile label="win rate" value={t.winRatePct !== null ? `${t.winRatePct.toFixed(0)}%` : "—"} />
        <PerfTile label="profit factor" value={fmtRatio(t.profitFactor)} />
        <PerfTile label="fee drag" value={t.feeDragPct !== null ? `${t.feeDragPct.toFixed(1)}%` : "—"} tone="dim" />
        <PerfTile label="completion" value={t.completionRatePct !== null ? `${t.completionRatePct.toFixed(0)}%` : "—"} />
        <PerfTile label="sharpe (daily, ann.)" value={fmtRatio(r.sharpe)} tone={perfTone(r.sharpe)} />
        <PerfTile label="sortino (daily, ann.)" value={fmtRatio(r.sortino)} tone={perfTone(r.sortino)} />
        <PerfTile label="calmar" value={fmtRatio(r.calmar)} tone={perfTone(r.calmar)} />
        <PerfTile
          label="max drawdown (daily)"
          value={perf.maxDrawdown !== null ? fmtMoney(perf.maxDrawdown) : "—"}
          tone="neg"
          title="peak-to-trough of the cumulative DAILY net; the calibration reading measures it trade by trade, so its figure can be larger"
        />
        <PerfTile label="recovery factor" value={fmtRatio(r.recoveryFactor)} tone={perfTone(r.recoveryFactor)} title="net P&L over the largest daily drawdown" />
      </TileGrid>
      {perf.equity.length === 0 ? <p className="muted">not enough history yet</p> : <EquityUnderwater equity={perf.equity} />}
      <BoxButtons>
        <BoxButton id="perf">by session, completion and calibration</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/** The performance lightbox's tabs: sessions, completion, live against paper, the calibration reading. */
export function PerformanceBox({ perf, tab }: { perf: LivePerformance; tab: string }) {
  if (tab === "completion") {
    return (
      <>
        <CompletionCard c={perf.completion} />
        <h3 className="lv-sub">Completion rate by session</h3>
        <CompletionTrend trend={perf.completionTrend} />
      </>
    );
  }
  if (tab === "paper") return perf.liveVsPaper !== null ? <LiveVsPaperCard lvp={perf.liveVsPaper} /> : <p className="muted">no paper book to set beside the live one</p>;
  if (tab === "calibration") {
    return perf.calibration.reading !== null ? (
      <>
        <p className="muted pf-caption" style={{ marginTop: 0 }}>
          per trade and per session, from {perf.calibration.from ?? "all time"}
        </p>
        <MetricTiles reading={perf.calibration.reading} peakRisk={perf.onRisk} />
      </>
    ) : (
      <p className={perf.calibration.error !== null ? "pnl-neg" : "muted"}>{perf.calibration.error ?? "no closed live trades for this arm in the window"}</p>
    );
  }
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>session</th>
            <th className="num">trades</th>
            <th className="num">net P&amp;L</th>
            <th className="num">peak risk</th>
            <th className="num">at</th>
            <th className="num">net / peak risk</th>
          </tr>
        </thead>
        <tbody>
          {perf.sessions.length === 0 ? (
            <tr>
              <td colSpan={6} className="muted">
                no settled live sessions
              </td>
            </tr>
          ) : (
            perf.sessions.map((s) => (
              <tr key={s.session}>
                <td>{s.session}</td>
                <td className="num">{s.trades}</td>
                <td className={`num ${tone(s.trades > 0 ? s.net : null)}`}>{s.trades > 0 ? fmtMoney(s.net) : "—"}</td>
                <td className="num" title="the session's largest open worst-case exposure — the figure the buying-power cap reads">
                  {s.peakRisk !== null ? fmtMoney(s.peakRisk) : "n/r"}
                </td>
                <td className="num">{hhmmOf(s.peakAt)}</td>
                <td className={`num ${tone(s.onRisk)}`} title={s.complete ? "" : "still open — the settled net is not the day's result yet"}>
                  {s.complete ? fmtReturn(s.onRisk) : "open"}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function hhmmOf(ts: string | null): string {
  return ts !== null && ts.length >= 16 ? ts.slice(11, 16) : "—";
}

function fillState(p: LivePosition): string {
  if (p.status === "settled") return "settled";
  if (p.status === "cancelled") return `cancelled (${p.entryFillStatus ?? "?"})`;
  if (p.entryFillStatus === "pending") return "entry working";
  if (p.completionFillStatus === "pending") return `completion resting${p.mark?.restingLimit != null ? ` @ ${p.mark.restingLimit.toFixed(2)}` : ""}`;
  if (p.kind === "fly") return "complete";
  return "open spread";
}

/**
 * A position the pilot holds as a trade card: the legs it was built from (a fly's body twice, its wings
 * once; a short vertical's pair), the credit it took in, the mark and the floor. The legs follow from the
 * ledger's structure, centre and wing; any other kind is drawn without them.
 */
export function positionModel(p: LivePosition, symbol: string): TradeCardModel {
  const q = p.quantity;
  const legs = structureLegs(p.kind, p.side, p.center, p.wingWidth).map((l) => ({ buy: l.buy, text: `${l.n * q} ${l.strike}${l.right}`, quote: null }));
  const credit = fmtPrice(p.netCredit);
  const clock = hhmmOf(p.entryTime);
  const done = p.status === "settled";
  return {
    key: p.positionId,
    symbol: `${symbol} ${p.center}`,
    spot: null,
    shape: `${p.kind === "fly" ? `${p.side} fly` : p.kind.replace(/_/g, " ")} · ${p.wingWidth} wide · ${clock}`,
    chips: <span className={`chip ${p.riskFree ? "chip-ok" : ""}`}>{fillState(p)}</span>,
    cash: p.netCredit * 100 * q,
    side: [`credit ${credit}`, `floor ${p.floorDollars === null ? "—" : fmtMoney(p.floorDollars)}`],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: p.riskFree ? "risk-free" : p.floorDollars === null ? "no floor stated" : `floor ${fmtMoney(p.floorDollars)}`,
    open: { date: clock, price: credit },
    legs,
    after: [
      done ? `settled ${p.pnl === null ? "—" : fmtMoney(p.pnl)}` : p.mark ? `mark ${fmtMoney(p.mark.markPnl)} at ${hhmmOf(p.mark.at)}` : "no usable mark",
    ],
    foot: [`entered ${clock}`, p.completedAt !== null ? `completed ${hhmmOf(p.completedAt)}` : fillState(p)],
  };
}

/** Every decision the pilot journaled today, a page at a time: the activity lightbox's body. */
function ActivityBody({ feed }: { feed: LiveFliesPayload["feed"] }) {
  const activity = usePage([feed.length], ACTIVITY_PAGE_SIZES[0]);
  return (
    <>
      <div className="table-scroll">
        <table className="data-table num-from-3">
          <thead>
            <tr>
              <th>last</th>
              <th>mode</th>
              <th>reason</th>
              <th className="num">×</th>
              <th className="num">centre</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {feed.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  no decisions journaled today
                </td>
              </tr>
            ) : (
              feed.slice(activity.page.offset, activity.page.offset + activity.page.limit).map((r, i) => (
                <tr key={activity.page.offset + i} className={r.accepted ? "" : "muted"}>
                  <td>{hhmmOf(r.lastSeen)}</td>
                  <td>{r.mode}</td>
                  <td>{r.reason}</td>
                  <td className="num">{r.occurrences}</td>
                  <td className="num">{r.centerLast ?? ""}</td>
                  <td>{r.detail ?? ""}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {feed.length > ACTIVITY_PAGE_SIZES[0] && (
        <Pager
          offset={activity.page.offset}
          limit={activity.page.limit}
          total={feed.length}
          pageSizes={ACTIVITY_PAGE_SIZES}
          onOffset={activity.setOffset}
          onLimit={activity.setLimit}
        />
      )}
    </>
  );
}

const PILOT_HELP: CardHelpContent = {
  title: "The flies pilot",
  body: (
    <>
      <HelpSection title="Settled, not marked">
        <p>
          Each period&apos;s net is settled sessions only: flies settles at expiry, so today reads zero until the bell. The paper book&apos;s same sessions sit beside it, and net is also
          shown as a share of the session&apos;s peak risk.
        </p>
      </HelpSection>
      <HelpSection title="The mark">
        <p>A mark is a mid, not a fill. Exposure is every open position&apos;s own worst case, net of fees and the assignment reserve: the figure the buying-power cap reads.</p>
      </HelpSection>
    </>
  ),
};

const INTRADAY_HELP: CardHelpContent = {
  title: "Intraday",
  body: (
    <HelpSection title="Two panels">
      <p>The underlying&apos;s percentage change with VIX and VIX3M, then the mark against exposure. The mark is a mid, not a fill.</p>
    </HelpSection>
  ),
};

const POSITIONS_HELP: CardHelpContent = {
  title: "Live positions",
  body: (
    <HelpSection title="A position">
      <p>Each fly or short vertical the pilot holds today: its legs, the credit it took in, its state, its floor and its mark. Activity, the journal of every decision, opens from the card.</p>
    </HelpSection>
  ),
};

export function LivePage() {
  const { data, isLoading, isError, dataUpdatedAt } = useLiveFlies();
  const d = data;
  const bp = d?.buyingPower;
  const acct = bp?.account ?? null;
  const capPct = bp && bp.cap ? Math.min(100, (bp.open / bp.cap) * 100) : null;
  const heldPeak = d !== undefined && d.today.open === 0 && d.today.pending === 0 ? d.today.sessionPeakWorst : null;
  // Before today's open the server hands back the last session the pilot settled; every "today"
  // on the page is that session, so the tile says which day it is.
  const lastCompleted = d?.sessionBasis === "last_completed";
  // Any session that is not today's running one: nothing on it is "now".
  const pastSession = d !== undefined && d.sessionBasis !== "today";
  const moduleOff = d?.moduleEnabled === false;
  // The day's positions in the order they were entered, as rows (a day holds many); the journal keeps its time order.
  const [view, setView] = useViewChoice("live-flies-positions", "rows");
  const boxes: Box[] =
    d === undefined
      ? []
      : [
          { id: "activity", kicker: "live positions ›", title: "Activity", tabs: [{ id: "feed", label: `${d.feed.length} decision${d.feed.length === 1 ? "" : "s"}` }], body: () => <ActivityBody feed={d.feed} /> },
          {
            id: "perf",
            kicker: "performance ›",
            title: "Performance in full",
            tabs: [
              { id: "sessions", label: "by session" },
              { id: "completion", label: "completion" },
              { id: "paper", label: "live vs paper" },
              { id: "calibration", label: "calibration" },
            ],
            body: (tab) => <PerformanceBox perf={d.performance} tab={tab} />,
          },
        ];

  return (
    <LiveBoxes boxes={boxes}>
      <div className="pf-layout">
        <Section concept="live" question="Is the pilot on?">
          <Card title="flies live" collapseKey="live-flies-pilot" help={PILOT_HELP}>
            {d && d.ledger !== "ok" && (
              <StateLine tone={d.ledger === "failed" ? "bad" : "note"}>
                {d.ledger === "absent" ? "no live ledger on this machine yet — the pilot has not traded here" : `live ledger read failed: ${d.ledgerError ?? "unknown"}`}
              </StateLine>
            )}
            {moduleOff && (
              <StateLine tone="warn">
                module off: flies is switched off in the suite config (<code>modules.flies.enabled</code> is false), so its loops, paper and live, are not scheduled and this tab reads
                history.
              </StateLine>
            )}
            {d && lastCompleted && (
              <StateLine>
                Showing <strong>{d.session}</strong>, the last session the pilot settled{moduleOff ? "" : " — today's session has not opened yet"}.
              </StateLine>
            )}
            <div className="stats-grid">
              {d && (
                <>
                  <PeriodTile label={pastSession ? `session ${d.session.slice(5)}` : "today"} p={d.periods.today} arm={d.arm.arm} />
                  <PeriodTile label="week" p={d.periods.week} arm={d.arm.arm} />
                  <PeriodTile label="month" p={d.periods.month} arm={d.arm.arm} />
                  <PeriodTile label="year" p={d.periods.year} arm={d.arm.arm} />
                  <div
                    className="stat-tile"
                    title={
                      pastSession
                        ? `the last tick of ${d.session}: the summed mid mark across what was open then — a mid, not a fill, and not now`
                        : "the latest tick's summed mid mark across open positions — a mid, not a fill"
                    }
                  >
                    <span className="stat-label">{markTileLabel(d)}</span>
                    <span className={`stat-value ${tone(d.today.markPnl)}`}>{signed(d.today.markPnl)}</span>
                    <span className="muted" style={{ display: "block", fontSize: 11 }}>
                      {d.today.markedAt !== null ? `at ${hhmmOf(d.today.markedAt)}` : "no mark yet"}
                      {d.today.maxDrawdown !== null ? ` · drawdown ${fmtMoney(d.today.maxDrawdown)}` : ""}
                    </span>
                  </div>
                  {heldPeak !== null ? (
                    // Settled: the last tick's exposure is the book just before the bell, which beside
                    // "0 open" reads as risk still on. The session's peak is what the day carried.
                    <div className="stat-tile" title="nothing open or working: the largest worst case at expiry the book carried during the session, from the loop's own per-tick exposure — shown while the page reads this session">
                      <span className="stat-label">worst case at expiry · session peak / cap</span>
                      <span className={`stat-value ${bp && bp.cap && -heldPeak.worst > bp.cap ? "pnl-neg" : ""}`}>
                        {`${fmtMoney(-heldPeak.worst)} / ${bp?.cap == null ? "no cap" : fmtMoney(bp.cap)}`}
                      </span>
                      <span className="muted" style={{ display: "block", fontSize: 11 }}>
                        {pastSession ? `${d.session} ` : ""}at {hhmmOf(heldPeak.at)} · nothing open ·{" "}
                        {d.today.completionPct !== null ? `${d.today.completionPct.toFixed(0)}% complete` : "no completions"}
                      </span>
                    </div>
                  ) : (
                    <div className="stat-tile" title="every open position's own worst case, net of fees and the assignment reserve — the same figure the buying-power cap reads">
                      <span className="stat-label">open exposure / cap</span>
                      <span className={`stat-value ${bp && bp.cap && bp.open > bp.cap ? "pnl-neg" : ""}`}>
                        {bp ? `${fmtMoney(bp.open)} / ${bp.cap === null ? "no cap" : fmtMoney(bp.cap)}` : "—"}
                      </span>
                      <span className="muted" style={{ display: "block", fontSize: 11 }}>
                        {capPct !== null ? `${capPct.toFixed(0)}% used · ` : ""}
                        {d.today.open} open · {d.today.pending} working · {d.today.completionPct !== null ? `${d.today.completionPct.toFixed(0)}% complete` : "no completions"}
                      </span>
                    </div>
                  )}
                  <div className="stat-tile" title={acct ? `broker account ${acct.account}, as of ${acct.at} — whole account, every module and manual trade included; a mid is not a fill` : bp?.accountError ?? "broker read unavailable"}>
                    <span className="stat-label">account · broker</span>
                    <span className="stat-value">
                      {acct?.derivativeBuyingPower !== null && acct?.derivativeBuyingPower !== undefined ? fmtMoney(acct.derivativeBuyingPower) : "—"}
                      <span className="muted" style={{ fontSize: 11 }}> BP free</span>
                    </span>
                    <span className="muted" style={{ display: "block", fontSize: 11 }}>
                      {acct
                        ? `NLV ${acct.netLiquidatingValue !== null ? fmtMoney(acct.netLiquidatingValue) : "—"} · day ${acct.dayPl !== null ? fmtMoney(acct.dayPl) : "—"} · ${acct.legCount ?? 0} legs${acct.unpricedCount ? `, ${acct.unpricedCount} unpriced` : ""}`
                        : (bp?.accountError ?? "unavailable")}
                    </span>
                  </div>
                </>
              )}
            </div>
          </Card>
        </Section>

        <Section concept="dir" question="What is it holding?">
          <Card title={`intraday · ${d?.session ?? ""}`} collapseKey="live-flies-intraday" className="lv-12" help={INTRADAY_HELP} updatedAt={dataUpdatedAt} isError={isError}>
            {d ? <LiveChart data={d} /> : <p className="muted">loading</p>}
          </Card>
          <Card
            title="live positions"
            collapseKey="live-flies-positions"
            className="lv-12"
            help={POSITIONS_HELP}
            updatedAt={dataUpdatedAt}
            isError={isError}
            controls={<ViewSwitch view={view} onChange={setView} />}
          >
            {isLoading ? (
              <p className="muted">loading</p>
            ) : (
              <ListViewContext.Provider value={view}>
                <TradeCards models={(d?.positions ?? []).map((p) => positionModel(p, d?.arm.symbol ?? "SPX"))} empty="nothing entered today" />
              </ListViewContext.Provider>
            )}
            <BoxButtons>
              <BoxButton id="activity">activity</BoxButton>
            </BoxButtons>
          </Card>
        </Section>

        {d && (
          <Section concept="hist" question="How has it done?">
            <PerformanceCard perf={d.performance} />
          </Section>
        )}
      </div>
    </LiveBoxes>
  );
}
