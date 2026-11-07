import { Fragment, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EARNINGS_WEEK_DAYS, type EarningsRecordRow, type EarningsRecordTotals, type EarningsRecordTrade } from "@console/shared";
import { useEarningsRecord } from "../../lib/api";
import { Card } from "../../components/DataTable";
import { Pager } from "../../components/ScopeBar";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { TileGrid } from "../../components/performance/TileGrid";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  FilterSwitch,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { ExpiryNote } from "./ExpiryNote";
import { ScreenerLayout } from "./ScreenerParts";
import { Section } from "../../components/Section";

/**
 * The earnings calendar's forward record (2026-10-17; docs/earnings-screener-plan.md, step 5): every
 * past announcement, what the calendar showed the session before it, and what happened. Read-only;
 * `technicals earnings-record` builds every figure each morning.
 *
 * 2026-11-06: the summary is a tiles card (the expiry-mixing bias behind its ?, the no-edge note an
 * amber flag) and the journal a paged list of rows with filter & sort, each row opening the spread's
 * money. Money follows the suite's layout: signed whole-position cash for one spread (a credit
 * received +, a debit paid -), entry + exit = gross, fees and the modelled slippage in their own
 * columns, net = gross - fees - slippage. The spread is entered at mid on its entry session's last
 * regular-hours reading and closed at mid on the reaction day's; a day-2 close is shown beside it.
 * The default order is newest first.
 */

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v < 0 ? "−" : v > 0 ? "+" : ""}$${s}`;
}

function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(100 * v).toFixed(digits)}%`;
}

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

const tone = (v: number | null | undefined) => (v === null || v === undefined ? "muted" : v >= 0 ? "pnl-pos" : "pnl-neg");

function Tile({ label, value, foot, cls, title }: { label: string; value: string; foot?: string; cls?: string; title?: string }) {
  return (
    <div className="stat-tile" title={title}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${cls ?? ""}`}>{value}</span>
      {foot ? <span className="stat-foot muted">{foot}</span> : null}
    </div>
  );
}

function totalsFoot(t: EarningsRecordTotals): string {
  return `${t.n} spreads · won ${pct(t.won)} · RoR ${pct(t.returnOnRisk, 1)}`;
}

export const recordHelp: CardHelpContent = {
  title: "Reading the forward check",
  body: (
    <>
      <HelpWarn title="No edge in history">
        <p>
          The forward check on the 2020-26 replay, which found no edge in the suggested spread (−4.3% on risk) and the direction read
          right on 48-49% of reactions. One spread a row, at mid on regular-hours readings; costs from the suite's fee schedule.
        </p>
      </HelpWarn>
      <HelpSection title="Inside the expected move">
        <p>
          Announcements whose one-day reaction is measured; of those, the ones the calendar showed an expected move for (0.85 × the
          at-the-money straddle on the first listed expiry), and the share that stayed inside it. The expiry is often weeks past the
          reaction, so this mixes one-day moves against fortnight and month moves, which leans toward inside. The second row keeps only
          the moves priced within {EARNINGS_WEEK_DAYS} days.
        </p>
      </HelpSection>
      <HelpSection title="The spreads">
        <p>
          Entered at mid on the entry session's last regular-hours reading, closed at mid on the reaction day, with a day-2 close beside
          it. Net is gross less fees and the modelled slippage, each in its own column.
        </p>
      </HelpSection>
    </>
  ),
};

export const JOURNAL_SORTS: SortOption<EarningsRecordRow>[] = [
  { id: "date", label: "report date", hint: "newest first", key: (r) => -Date.parse(`${r.date}T00:00:00Z`) },
  { id: "move", label: "|move| / expected move", hint: "largest first", key: (r) => (r.moveOverEm === null ? null : -Math.abs(r.moveOverEm)) },
  { id: "net", label: "net", hint: "worst first", key: (r) => r.spread?.react?.net ?? null },
];

type Show = "outside" | "called" | "bull" | "bear";
const SHOW_WORDS: Record<Show, string> = { outside: "outside the move", called: "direction called", bull: "strong bull reads", bear: "strong bear reads" };
const PAGE_SIZES_HERE = [25, 50, 100] as const;

export function matches(r: EarningsRecordRow, on: Set<Show>): boolean {
  return (
    (!on.has("outside") || r.insideEm === false) &&
    (!on.has("called") || r.directionRight === true) &&
    (!on.has("bull") || r.edge === "sb") &&
    (!on.has("bear") || r.edge === "sbe")
  );
}

export function MoneyDetail({ r }: { r: EarningsRecordRow }) {
  const s = r.spread;
  if (s === null) return <p className="muted">No suggested spread: not a strong read, or no entry session (time of day unknown).</p>;
  if (s.state !== "entered" || s.kind === null) return <p className="muted">The spread was never entered: {s.state}.</p>;
  const t: EarningsRecordTrade | null = s.react;
  const d2 = s.day2;
  // The strikes come [long, short]; a credit spread reads short first, as on the calendar.
  const strikes = (s.kind === "credit" ? [...(s.strikes ?? [])].reverse() : (s.strikes ?? [])).join("/");
  return (
    <>
      <p className="muted" style={{ margin: "0 0 8px" }}>
        {s.kind} {s.right === "C" ? "call" : "put"} {strikes} · {s.expiry} · {s.liquidAtEntry === false ? "thin at entry" : s.liquidAtEntry ? "liquid at entry" : "liquidity unread"}
        {" · "}calendar of {r.calendarDay}, reacted {r.reactionDay}
      </p>
      <TileGrid count={6}>
        <Tile label="entry" value={money(t?.entry)} title="Opening at mid: a credit received +, a debit paid −" />
        <Tile label="exit" value={money(t?.exit)} title="Closing at mid on the reaction day" />
        <Tile label="gross" value={money(t?.gross)} title="entry + exit" />
        <Tile label="fees" value={t ? money(-t.fees) : "—"} />
        <Tile label="slippage" value={t ? money(-t.slippage) : "—"} title="Modelled slippage: fills at mid do not concede it" />
        <Tile label="net" value={money(t?.net)} cls={tone(t?.net)} foot={d2 ? `day 2: ${money(d2.net)}` : "no day 2"} title="Net had it closed the session after instead: the foot" />
      </TileGrid>
    </>
  );
}

export function EarningsRecordTab() {
  const { data, isLoading, isError } = useEarningsRecord();
  const s = data?.summary ?? null;
  const rows = data?.rows ?? [];
  const [sortChoice, setSortChoice] = useSortChoice("earnings-journal", "date");
  const [show, setShow] = useState<Set<Show>>(new Set());
  const [panel, setPanel] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState<number>(25);
  const anchor = useRef<HTMLButtonElement>(null);

  const sortOption = JOURNAL_SORTS.find((o) => o.id === sortChoice.id);
  const listed = sortRows(rows.filter((r) => matches(r, show)), sortOption, sortChoice.reversed);
  const page = listed.slice(offset, offset + limit);
  const changed = show.size + (sortChoice.id !== "date" || sortChoice.reversed ? 1 : 0);
  const reset = () => {
    setShow(new Set());
    setSortChoice({ id: "date", reversed: false });
    setOffset(0);
  };
  const toggleShow = (k: Show, on: boolean) => {
    const next = new Set(show);
    if (on) next.add(k);
    else next.delete(k);
    setShow(next);
    setOffset(0);
  };
  const toggleOpen = (k: string) => {
    const next = new Set(open);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setOpen(next);
  };

  return (
    <ScreenerLayout>
      {isError && <p className="muted">Could not read the earnings record.</p>}
      {isLoading && <p className="muted">Reading the record…</p>}
      {data && data.asOf === null && (
        <p className="muted">No record yet: `technicals earnings-record` writes it each morning at 06:50 ET, once announcements on the calendar have reacted.</p>
      )}
      {s && (
        <Section concept="hist" tag="record" question="How did past suggestions do?">
          <Card title="forward check" help={recordHelp} flag="no edge in history" controls={data?.asOf ? <span className="card-asof">as of {data.asOf}</span> : null}>
            <TileGrid count={6}>
              <Tile label="measured" value={`${s.measuredMoves ?? "—"}`} foot={`of ${s.announcements} announcements`} />
              <Tile label="inside the move" value={pct(s.insideEm)} foot={`of ${s.compared} with an expected move`} />
              <Tile label="median |move| / EM" value={s.medianMoveOverEm?.toFixed(2) ?? "—"} foot="above 1 is outside it" />
              <Tile label="direction called" value={pct(s.directionRight)} foot={`of ${s.strongReads} strong reads`} />
              <Tile label="net, reaction day" value={money(s.react.net)} cls={tone(s.react.net)} foot={totalsFoot(s.react)} />
              <Tile label="net, day 2" value={money(s.day2.net)} cls={tone(s.day2.net)} foot={totalsFoot(s.day2)} />
            </TileGrid>
            {s.earningsWeek && (
              <div style={{ marginTop: 8 }}>
                <TileGrid count={3}>
                  <Tile label={`priced within ${s.earningsWeek.maxDays} days`} value={`${s.earningsWeek.n}`} foot="an earnings move, not a month's" />
                  <Tile label="inside the move" value={pct(s.earningsWeek.insideEm)} />
                  <Tile label="median |move| / EM" value={s.earningsWeek.medianMoveOverEm?.toFixed(2) ?? "—"} />
                </TileGrid>
              </div>
            )}
          </Card>
        </Section>
      )}
      {rows.length > 0 && (
        <Section concept="hist" tag="every announcement" question="What happened each time?">
          <Card
            title="journal"
            help={{
              title: "The journal",
              body: (
                <HelpSection title="A row">
                  <p>
                    What the calendar showed before the report and what happened: the move against the expected move, and the simulated
                    spread's net after fees and slippage. Open a row for the spread's entry, exit, gross, fees, slippage and net.
                  </p>
                </HelpSection>
              ),
            }}
            controls={<FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />}
          >
            <FilterSummary
              parts={[`by ${sortOption?.label ?? sortChoice.id}${sortChoice.reversed ? ", reversed" : `, ${sortOption?.hint ?? ""}`}`, ...[...show].map((k) => SHOW_WORDS[k])]}
              note={`${listed.length.toLocaleString()} of ${rows.length.toLocaleString()}`}
              changed={changed}
              onOpen={() => setPanel(true)}
              onReset={reset}
            />
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th aria-label="open" />
                    <th>Symbol</th>
                    <th>Report</th>
                    <th>Direction</th>
                    <th
                      className="num"
                      title={`The expected move the calendar showed the session before: 0.85 × the at-the-money straddle on the first listed expiry, with that expiry and its days past the reaction. Past ${EARNINGS_WEEK_DAYS} days it prices a fortnight's or a month's move, so a one-day reaction sits inside it more easily.`}
                    >
                      Exp. move to expiry
                    </th>
                    <th className="num" title="The reaction-day close against the session before's">
                      Move
                    </th>
                    <th className="num" title="The move's size over the expected move: above 1 is outside it">
                      |Move| / exp.
                    </th>
                    <th title="Strong reads only: did the reaction go the read's way">Called</th>
                    <th className="num" title="Gross less fees and slippage, closed on the reaction day">
                      Net
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.map((r) => {
                    const k = `${r.symbol}-${r.date}`;
                    const isOpen = open.has(k);
                    const net = r.spread?.react ?? null;
                    return (
                      <Fragment key={k}>
                        <tr>
                          <td>
                            <button
                              type="button"
                              className={`btn btn-quiet collapse-toggle ${isOpen ? "collapse-toggle-open" : ""}`}
                              aria-expanded={isOpen}
                              aria-label={`${isOpen ? "Close" : "Open"} ${r.symbol}`}
                              onClick={() => toggleOpen(k)}
                            >
                              ▸
                            </button>
                          </td>
                          <td>
                            <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`}>
                              {r.symbol}
                            </Link>
                          </td>
                          <td title={`calendar of ${r.calendarDay}; reacted ${r.reactionDay}`}>
                            {shortDate(r.date)} <span className="muted">{r.timing ? r.timing.toUpperCase() : "time unknown"}</span>
                          </td>
                          <td>{r.edgeLabel ?? "—"}</td>
                          <td className="num">
                            {r.emMovePct === null ? "—" : `${r.emMovePct.toFixed(1)}%`}
                            {r.emMovePct !== null && <ExpiryNote expiry={r.eventExpiry} days={r.eventExpiryDays} />}
                          </td>
                          <td className={`num ${r.movePct === null ? "muted" : r.movePct >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                            {r.movePct === null ? "—" : `${r.movePct > 0 ? "+" : r.movePct < 0 ? "−" : ""}${Math.abs(r.movePct).toFixed(1)}%`}
                          </td>
                          <td className="num">
                            {r.moveOverEm === null ? "—" : r.moveOverEm.toFixed(2)}
                            {r.insideEm === false && <span className="muted"> outside</span>}
                          </td>
                          <td className={r.directionRight === null ? "muted" : r.directionRight ? "pnl-pos" : "pnl-neg"}>
                            {r.directionRight === null ? "—" : r.directionRight ? "yes" : "no"}
                          </td>
                          <td className={`num ${net ? tone(net.net) : "muted"}`} title={net ? `gross ${money(net.gross)} · fees ${money(-net.fees)} · slippage ${money(-net.slippage)} = net ${money(net.net)}` : undefined}>
                            {net ? money(net.net) : "—"}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="ec-open">
                            <td colSpan={9}>
                              <MoneyDetail r={r} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {listed.length === 0 && <p className="muted">Nothing matches these filters.</p>}
            <Pager offset={offset} limit={limit} total={listed.length} pageSizes={PAGE_SIZES_HERE} onOffset={setOffset} onLimit={(v) => setLimit(v)} />
          </Card>
          <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
            <SortSection sorts={JOURNAL_SORTS} choice={sortChoice} fallback="date" onChange={(c) => { setSortChoice(c); setOffset(0); }} />
            <FilterGroup title="outcome">
              <FilterSwitch label="Outside the expected move" description="The reaction went further than the move the calendar showed" on={show.has("outside")} onChange={(v) => toggleShow("outside", v)} />
              <FilterSwitch label="Direction called" description="Strong reads whose reaction went their way" on={show.has("called")} onChange={(v) => toggleShow("called", v)} />
            </FilterGroup>
            <FilterGroup title="direction" note="our read the session before">
              <FilterSwitch label="Strong bull reads" description="Only announcements read strong bull" on={show.has("bull")} onChange={(v) => toggleShow("bull", v)} />
              <FilterSwitch label="Strong bear reads" description="Only announcements read strong bear" on={show.has("bear")} onChange={(v) => toggleShow("bear", v)} />
            </FilterGroup>
          </FilterSortPanel>
        </Section>
      )}
    </ScreenerLayout>
  );
}
