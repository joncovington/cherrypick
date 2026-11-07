import type { CalendarsPayload, CalendarsPosition } from "@console/shared";
import { Card, DataCard, fmtMoney, fmtNum, fmtPct, PnlCell, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import type { SortOption } from "../../components/FilterSort";
import { fmtCash, fmtPrice } from "../../lib/format";
import { dayMonth, type TradeCardLeg, type TradeCardModel } from "../Live/TradeCard";
import { TradeListCard } from "../Live/TradeListCard";

const PLAN_HELP: CardHelpContent = {
  title: "This week",
  body: (
    <HelpSection title="The structure tag">
      <p>
        The tag is calendar days from entry to each expiration. A Tuesday entry after a Monday holiday makes <span className="mono">dc_3_6</span>,
        and a dark following Monday makes <span className="mono">dc_4_8</span>: different trades, never pooled with{" "}
        <span className="mono">dc_4_7</span>. The anchors are the module&rsquo;s own holiday arithmetic and are never re-derived here.
      </p>
    </HelpSection>
  ),
};

const ENTRY_HELP: CardHelpContent = {
  title: "Entry window",
  body: (
    <>
      <HelpSection title="One row per outcome">
        <p>
          One uncollapsed row per evaluated tick, grouped here by outcome. Entry is retried every tick until it fills or the window closes;
          an exhausted window is a journaled skipped week, and the week does not come back.
        </p>
      </HelpSection>
      <HelpSection title="A refusal, not a missing setup">
        <p>
          Entry is unconditional, so a week with no position is a refusal, and each refusal is a different thing to do about it. Hover a
          ? beside an outcome for its meaning.
        </p>
      </HelpSection>
    </>
  ),
};

const POSITIONS_HELP: CardHelpContent = {
  title: "Structures",
  body: (
    <>
      <HelpSection title="A structure">
        <p>
          One side (put or call) of one arm&rsquo;s double calendar: the order that opened it is the front short and the back long. Cash is
          the opening debit for the whole position (negative: paid). The net is realised once the week settles and the mark to market,
          after costs to date, before then.
        </p>
      </HelpSection>
      <HelpSection title="Same plan">
        <p>
          Every arm&rsquo;s positions for a week come from the <strong>same</strong> entry plan: identical strikes, identical mids, identical
          modeled costs. Any divergence between arms is exit policy and nothing else.
        </p>
      </HelpSection>
    </>
  ),
};

const RESULTS_HELP: CardHelpContent = {
  title: "Results by arm",
  body: (
    <HelpSection title="Each row adds up">
      <p>
        <span className="mono">gross − fees − settle − slip = net</span>. Fills are modelled at mid, so slippage is a charged cost here. Rows
        are per structure tag and are never summed across tags.
      </p>
    </HelpSection>
  ),
};

/**
 * The module's own refusal vocabulary, each in one plain sentence.
 *
 * Entry here is deliberately unconditional — the experiment is about exits — so a week with no
 * position is never "the setup wasn't there". It is a refusal, and every refusal below is a
 * different thing to do about it: a lapsed dividend table is a config task, `ex_dividend_week` is
 * the design working, and `no_fresh_quotes` is the streamer. A raw token forces the reader to go
 * and find out which.
 *
 * Anything not listed renders as its raw token rather than a guess — a wrong explanation of a
 * refusal is worse than an unexplained one.
 */
const OUTCOMES: Record<string, string> = {
  filled: "the structure was priced and both sides opened.",
  no_fresh_quotes:
    "every option quote near spot was older than the module's max age, so nothing could be priced. This is the streamer, not the market.",
  no_spot_price: "the underlying's own price would not print from the stream cache.",
  stream_cache_missing: "the shared stream cache could not be opened at all.",
  no_front_chain: "the cache held no chain rows for the Friday expiration.",
  no_back_chain: "the cache held no chain rows for the following Monday expiration.",
  not_weekly_listed:
    "the expiration is listed, but not under the configured weekly root — a third-Friday week where only the AM-settled monthly exists. Skipped rather than traded on the wrong root.",
  no_strikes_near_spot: "no listed strikes fell inside the entry snapshot's window around spot.",
  no_em_quotes: "the front ATM straddle would not price, so there is no expected move to target.",
  no_intersection_strike:
    "no strike is listed in BOTH expirations at the expected-move target, so no calendar can be built there.",
  non_positive_debit: "the structure priced at a zero or negative debit, which is not a calendar.",
  ex_dividend_week:
    "a declared ex-dividend date falls inside the week. Skipped by design, not modelled — an ITM short call is really assigned the session before the ex-date, ahead of anything this module arms.",
  dividend_calendar_lapsed:
    "the week runs past the declared dividend calendar. Entry is refused rather than assuming the week dividend-free — a lapsed table stops entries loudly, by design.",
  unknown_settlement:
    "the symbol is declared as neither cash- nor physically-settled. Refused rather than assumed into a style.",
  week_skipped_entry_window_exhausted:
    "the entry window closed with nothing filled. Entry is attempted on the entry day only, so the week is now gone.",
};

function explain(outcome: string): string | null {
  return OUTCOMES[outcome] ?? null;
}

/** The week's computed anchors — the skeleton every other card on the page hangs off. */
export function PlanCard({ data, updatedAt }: { data: CalendarsPayload | undefined; updatedAt?: number }) {
  const plan = data?.plan;
  return (
    <Card title="this week" collapseKey="cal-plan" updatedAt={updatedAt} className="view-fade" help={PLAN_HELP}>
      {plan === null || plan === undefined ? (
        <p className="muted">
          {data?.planError ?? "the week plan is not available"}
          {data?.planError !== null && data?.planError !== undefined && (
            <span className="integrity-note">
              {" "}
              The dates below the fold come from the ledger instead; the anchors are the module&rsquo;s own
              holiday arithmetic and are never re-derived here.
            </span>
          )}
        </p>
      ) : (
        <div className="cal-plan">
          <div className="cal-plan-anchors">
            <div>
              <span className="cal-plan-label">week of</span>
              <span className="cal-plan-value mono">{plan.weekOf}</span>
            </div>
            <div>
              <span className="cal-plan-label">entry</span>
              <span className="cal-plan-value mono">{plan.entrySession}</span>
              <span className="cal-plan-when">
                {data?.params.entryWindowStart ?? "—"}–{data?.params.entryWindowEnd ?? "—"} ET
              </span>
            </div>
            <div>
              <span className="cal-plan-label">shorts expire</span>
              <span className="cal-plan-value mono">{plan.frontExpiration}</span>
            </div>
            <div>
              <span className="cal-plan-label">longs expire</span>
              <span className="cal-plan-value mono">{plan.backExpiration}</span>
            </div>
            <div>
              <span className="cal-plan-label">structure</span>
              <span className="cal-plan-value mono">{plan.structure}</span>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

/**
 * What the entry day did with its one window.
 *
 * This card leads the page when the week holds nothing, which for a once-a-week strategy is most of
 * what a reader will ever open the page to ask. It answers in the module's own words and then puts
 * the feed counts underneath, because on this module's first scheduled Monday the whole story was
 * two numbers: zero fresh quotes against 248 stale ones, on every tick of a fifteen-minute window.
 */
export function EntryWindowCard({ data, updatedAt }: { data: CalendarsPayload | undefined; updatedAt?: number }) {
  const w = data?.entryWindow;
  const skipText = w?.skipReason === null || w?.skipReason === undefined ? null : explain(w.skipReason);

  return (
    <Card title="entry window" collapseKey="cal-entry" updatedAt={updatedAt} className="view-fade" help={ENTRY_HELP}>
      {w === undefined || w.session === null ? (
        <p className="muted">no entry session on file yet</p>
      ) : (
        <>
          <div className={w.entered ? "cal-entry-banner cal-entry-ok" : "cal-entry-banner"}>
            {w.entered ? (
              <>
                <strong>Entered.</strong> <span className="mono">{w.session}</span> opened its structure inside
                the {w.windowStart ?? "—"}–{w.windowEnd ?? "—"} ET window.
              </>
            ) : (
              <>
                <strong>No position.</strong> <span className="mono">{w.session}</span> is an entry day and
                entry is unconditional, so this is a refusal rather than a missing setup
                {w.skipReason !== null && (
                  <>
                    {" "}
                    — <span className="mono">{w.skipReason}</span>
                    {w.skipOccurrences > 0 && <span className="muted"> ×{w.skipOccurrences}</span>}
                  </>
                )}
                {skipText === null ? "." : <span className="cal-note-inline">: {skipText}</span>}
              </>
            )}
          </div>

          {w.feed !== null && (
            <p className="cal-feed">
              <span className="cal-plan-label">feed</span>
              {w.feed.ticks} entry snapshot{w.feed.ticks === 1 ? "" : "s"} ·{" "}
              <span className={w.feed.fresh === 0 ? "integrity-warn" : ""}>
                {w.feed.fresh.toLocaleString()} fresh quote{w.feed.fresh === 1 ? "" : "s"}
              </span>{" "}
              against {w.feed.stale.toLocaleString()} rejected as stale · spot printed on {w.feed.spotTicks} of
              them
              {data?.params.maxQuoteAgeSeconds !== null && data?.params.maxQuoteAgeSeconds !== undefined && (
                <span className="muted"> (max quote age {data.params.maxQuoteAgeSeconds}s)</span>
              )}
            </p>
          )}

          <div className="table-scroll">
          <table className="data-table num-from-1 cal-attempts">
            <thead>
              <tr>
                <th>outcome</th>
                <th>ticks</th>
                <th>first</th>
                <th>last</th>
                <th>spot</th>
                <th>EM</th>
                <th>put / call strike</th>
                <th>debit</th>
              </tr>
            </thead>
            <tbody>
              {w.attempts.length === 0 ? (
                <tr>
                  <td colSpan={8} className="muted">
                    no entry opportunities evaluated on this session
                  </td>
                </tr>
              ) : (
                w.attempts.map((a) => (
                  <tr key={a.outcome}>
                    <td>
                      <span className="mono">{a.outcome}</span>
                      {explain(a.outcome) !== null && (
                        <span className="cal-help" title={explain(a.outcome) ?? ""}>
                          ?
                        </span>
                      )}
                    </td>
                    <td>{a.n}</td>
                    <td className="mono">{a.firstTs?.slice(11, 16) ?? "—"}</td>
                    <td className="mono">{a.lastTs?.slice(11, 16) ?? "—"}</td>
                    <td>{fmtNum(a.spot, 2)}</td>
                    <td>{fmtNum(a.em, 2)}</td>
                    <td>
                      {a.putStrike === null && a.callStrike === null
                        ? "—"
                        : `${fmtNum(a.putStrike, 0)} / ${fmtNum(a.callStrike, 0)}`}
                    </td>
                    <td>
                      {a.putDebit === null && a.callDebit === null
                        ? "—"
                        : `${fmtNum(a.putDebit, 2)} / ${fmtNum(a.callDebit, 2)}`}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          </div>
        </>
      )}
    </Card>
  );
}

export const CALENDAR_SORTS: SortOption<CalendarsPosition>[] = [
  { id: "time", label: "entry session", hint: "newest first", key: (p) => (p.entrySession === "" ? null : -Date.parse(p.entrySession)) },
  { id: "expiry", label: "expiry", hint: "soonest first", key: (p) => p.frontExpiration },
  { id: "size", label: "size", hint: "largest debit first", key: (p) => (p.entryCash) },
  { id: "arm", label: "arm", hint: "A to Z", key: (p) => p.arm },
];

/** One side of a double calendar as a trade card: the front short and the back long, as the order that opened it. */
export function calendarPositionModel(p: CalendarsPosition): TradeCardModel {
  const q = p.quantity ?? 1;
  const right = p.side.toLowerCase().startsWith("c") ? "C" : "P";
  const net = p.netPnl !== null ? p.netPnl : p.unrealisedNet;
  const legs: TradeCardLeg[] =
    p.legs.length > 0
      ? [...p.legs].sort((a, b) => a.expiration.localeCompare(b.expiration)).map((l) => ({
          buy: l.action.toLowerCase().startsWith("buy"),
          text: `${String(q)} ${fmtNum(l.strike ?? p.strike, 0)}${right} ${dayMonth(l.expiration)}`,
          quote: l.entryMid === null ? null : `mid ${fmtNum(l.entryMid, 2)}`,
        }))
      : [];
  return {
    key: p.positionId,
    symbol: p.symbol,
    spot: p.entrySpot === null ? null : `entry spot ${fmtNum(p.entrySpot, 2)}`,
    shape: `${p.side} calendar @ ${fmtNum(p.strike, 0)} · ${p.structure}`,
    chips: (
      <>
        <span className="chip">{p.arm}</span>
        <span className="chip">{p.status}</span>
      </>
    ),
    cash: p.entryCash,
    side: [`${fmtPrice(p.entryDebit === null ? null : -p.entryDebit)} debit`, `qty ${p.quantity === null ? "—" : String(p.quantity)}`],
    bar: null,
    barTitle: "",
    hideBar: true,
    label: net === null ? "no usable mark yet" : p.netPnl !== null ? `net ${fmtMoney(p.netPnl)} · realised` : `marked net ${fmtMoney(net)} · after costs to date`,
    open: { date: p.entrySession === "" ? "—" : p.entrySession.slice(5), price: fmtPrice(p.entryDebit === null ? null : -p.entryDebit) },
    legs,
    after: [
      `front ${p.frontExpiration} / back ${p.backExpiration}`,
      `EM ${fmtNum(p.entryEm, 2)}${p.entryEmPct !== null ? ` (${fmtPct(p.entryEmPct * 100, 2)})` : ""}`,
      `IV front ${fmtPct(p.entryFrontIv === null ? null : p.entryFrontIv * 100, 1)} · back ${fmtPct(p.entryBackIv === null ? null : p.entryBackIv * 100, 1)} · term ${fmtNum(p.entryTermStructure, 3)}`,
      ...(p.exitReason !== null ? [`exit: ${p.exitReason}`] : []),
    ],
    foot: [p.entrySession === "" ? "—" : `opened ${p.entrySession}`, p.status],
  };
}

/**
 * The structures the ledger currently holds, as trade cards (or rows).
 *
 * Arms, not a fixed grid: every arm enters from the same plan, so a arm missing from this list
 * is a arm that did not enter, and a row of em-dashes would have invited that to read as a broken
 * number instead.
 */
export function PositionsCard({
  positions,
  title,
  emptyText,
  updatedAt,
  loading = false,
}: {
  positions: CalendarsPosition[];
  title: string;
  emptyText: string;
  updatedAt?: number;
  loading?: boolean;
}) {
  return (
    <TradeListCard
      card={`cal-${title.replace(/\s+/g, "-")}`}
      title={title}
      help={POSITIONS_HELP}
      rows={positions}
      model={calendarPositionModel}
      sorts={CALENDAR_SORTS}
      fallback="time"
      empty={emptyText}
      updatedAt={updatedAt}
      loading={loading}
    />
  );
}

/** Per-arm, per-structure results over closed positions — the module's headline. */
export function BookComparison({ data, updatedAt }: { data: CalendarsPayload | undefined; updatedAt?: number }) {
  const sort = useSort();
  // Costs are shown negated (money out), and sort as shown.
  const arms = sortRows(data?.arms ?? [], sort, {
    arm: (b) => b.arm,
    structure: (b) => b.structure,
    weeks: (b) => b.weeks,
    positions: (b) => b.positions,
    gross: (b) => b.grossPnl,
    fees: (b) => (b.fees === null ? null : -b.fees),
    settle: (b) => (b.settlementFees === null ? null : -b.settlementFees),
    slip: (b) => (b.slippage === null ? null : -b.slippage),
    net: (b) => b.netPnl,
    winRate: (b) => b.winRate,
  });
  return (
    <DataCard
      title="results by arm"
      sort={sort}
      headers={[
        { label: "arm", k: "arm" },
        { label: "structure", k: "structure" },
        { label: "weeks", k: "weeks" },
        { label: "positions", k: "positions" },
        { label: "gross", k: "gross" },
        { label: "fees", k: "fees" },
        { label: "settle", k: "settle" },
        { label: "slip", k: "slip" },
        { label: "net", k: "net" },
        { label: "win rate", k: "winRate" },
      ]}
      loading={data === undefined}
      rowCount={arms.length}
      numFrom={2}
      empty="no week has closed yet — the arms have nothing to report"
      updatedAt={updatedAt}
      help={RESULTS_HELP}
    >
      {arms.map((b) => (
        <tr key={`${b.arm}-${b.structure}`}>
          <td>
            <span className="mono">{b.arm}</span>
          </td>
          <td>
            <span className="mono">{b.structure}</span>
          </td>
          <td>{b.weeks}</td>
          <td>{b.positions}</td>
          <td>{fmtMoney(b.grossPnl)}</td>
          <td>{fmtMoney(b.fees === null ? null : -b.fees)}</td>
          <td>{fmtMoney(b.settlementFees === null ? null : -b.settlementFees)}</td>
          <td>{fmtMoney(b.slippage === null ? null : -b.slippage)}</td>
          <td>
            <PnlCell v={b.netPnl} />
          </td>
          <td>{fmtPct(b.winRate === null ? null : b.winRate * 100, 0)}</td>
        </tr>
      ))}
    </DataCard>
  );
}
