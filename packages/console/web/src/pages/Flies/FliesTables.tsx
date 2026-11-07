import { useRef, useState } from "react";
import type { FliesBookRow, FliesPayload, FliesPositionRow } from "@console/shared";
import { Card, PnlCell, SkeletonRows, fmtMoney, fmtNum } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Pager } from "../../components/ScopeBar";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { fmtCash, fmtPrice } from "../../lib/format";
import { TradeCards, type TradeCardModel } from "../Live/TradeCard";
import { ChoiceGroup, FliesLayout, FliesSection } from "./FliesFilter";
import { clockTime, structureLabel, wingWidth } from "./structure";

/**
 * The positions and books tabs (2026-11-06), in the portfolio tab's layout: a position is a trade card
 * (or a row), a book a row of the table, each card with its filter & sort and a pager. The reader pages
 * on the server, newest first, so a sort or a filter here orders and narrows the page in view, and the
 * summary line says so rather than implying it ranked the whole set.
 */

type Paging = {
  page: { offset: number; limit: number };
  setOffset: (v: number) => void;
  setLimit: (v: number) => void;
};

interface Props {
  data: FliesPayload | undefined;
  paging: Paging;
  loading: boolean;
  isError: boolean;
  busy: boolean;
  /** The arm or day is narrowed: the empty message says so. */
  narrowed: boolean;
}

/* ---------------------------------------------------------------- positions */

export const POSITION_HELP: CardHelpContent = {
  title: "Positions",
  body: (
    <>
      <HelpSection title="A position">
        <p>
          One entry: its kind, centre, wing, quantity, price, entry cash and floor. It is risk-free when the floor is at or above zero.
          Cancelled entries and rows struck from the record are not listed.
        </p>
      </HelpSection>
      <HelpSection title="Filter and sort">
        <p>The reader pages newest first, so a sort or a filter orders the page in view; turn the page to read the others.</p>
      </HelpSection>
    </>
  ),
};

export const POSITION_SORTS: SortOption<FliesPositionRow>[] = [
  { id: "time", label: "entry time", hint: "newest first", key: (p) => (p.entryTime ? -Date.parse(p.entryTime) : null) },
  { id: "arm", label: "arm", hint: "A to Z", key: (p) => p.arm },
  { id: "floor", label: "floor", hint: "worst first", key: (p) => p.floorDollars },
];

export type PositionShow = "all" | "risk" | "free";
const SHOW: ReadonlyArray<{ id: PositionShow; label: string }> = [
  { id: "all", label: "all" },
  { id: "risk", label: "at risk" },
  { id: "free", label: "risk-free" },
];

export function positionShown(p: FliesPositionRow, show: PositionShow): boolean {
  return show === "all" || (show === "free" ? p.riskFree : !p.riskFree);
}

export function positionModel(p: FliesPositionRow): TradeCardModel {
  const wing = wingWidth(p.wingWidth, p.farWidth);
  return {
    key: p.positionId,
    symbol: p.symbol,
    symbolHref: `/charts/technicals?symbol=${encodeURIComponent(p.symbol)}`,
    spot: null,
    shape: `${structureLabel(p.kind, p.side)} · ${fmtNum(p.center, 0)}${wing === "—" ? "" : ` · ${wing} wide`}`,
    chips: (
      <>
        {p.arm ? <span className="chip">{p.arm}</span> : null}
        {p.riskFree ? <span className="chip chip-ok">risk-free</span> : <span className="chip chip-warn">at risk</span>}
      </>
    ),
    cash: p.entryCash,
    side: [fmtPrice(p.net), p.quantity === null ? "qty —" : `qty ${p.quantity}`],
    bar: null,
    barTitle: "",
    label: `floor ${fmtCash(p.floorDollars)}`,
    open: { date: `${p.tradeDate.slice(5)} ${clockTime(p.entryTime)}`, price: fmtPrice(p.net) },
    legs: [],
    after: [p.status, ...(p.pnl !== null ? [`net ${fmtMoney(p.pnl)}`] : [])],
    foot: [`entry mode: ${p.entryMode ?? "—"}`, p.status],
  };
}

export function PositionsCard({ data, paging, loading, isError, narrowed }: Props) {
  const rows = data?.positions.rows ?? [];
  const total = data?.positions.total ?? 0;
  const [view, setView] = useViewChoice("flies-positions", "cards");
  const [sortChoice, setSortChoice] = useSortChoice("flies-positions", "time");
  const [show, setShow] = useState<PositionShow>("all");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const option = POSITION_SORTS.find((o) => o.id === sortChoice.id);
  const listed = sortRows(rows.filter((p) => positionShown(p, show)), option, sortChoice.reversed);
  const changed = (show !== "all" ? 1 : 0) + (sortChoice.id !== "time" || sortChoice.reversed ? 1 : 0);
  const reset = () => {
    setShow("all");
    setSortChoice({ id: "time", reversed: false });
  };
  return (
    <FliesLayout>
      <FliesSection concept="dir" name="entries" question="What did each arm enter?">
        <Card
          title="positions"
          help={POSITION_HELP}
          isError={isError}
          controls={
            <>
              <ViewSwitch view={view} onChange={setView} />
              <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />
            </>
          }
        >
          <FilterSummary
            parts={[`by ${option?.label ?? sortChoice.id}${sortChoice.reversed ? ", reversed" : `, ${option?.hint ?? ""}`}`, ...(show === "all" ? [] : [show === "free" ? "risk-free" : "at risk"])]}
            note={`${listed.length.toLocaleString()} of ${rows.length.toLocaleString()} on this page · ${total.toLocaleString()} matching`}
            changed={changed}
            onOpen={() => setPanel(true)}
            onReset={reset}
          />
          {loading ? (
            <p className="muted">reading…</p>
          ) : (
            <ListViewContext.Provider value={view}>
              <TradeCards models={listed.map(positionModel)} empty={rows.length > 0 ? "Nothing matches these filters on this page." : narrowed ? "no matching positions" : "no positions today"} />
            </ListViewContext.Provider>
          )}
          {total > 0 && (
            <div className="card-footer">
              <Pager offset={data?.positions.offset ?? paging.page.offset} limit={data?.positions.limit ?? paging.page.limit} total={total} onOffset={paging.setOffset} onLimit={paging.setLimit} />
            </div>
          )}
        </Card>
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          <SortSection sorts={POSITION_SORTS} choice={sortChoice} fallback="time" onChange={setSortChoice} />
          <FilterGroup title="show">
            <ChoiceGroup label="show" value={show} options={SHOW} onChange={setShow} />
          </FilterGroup>
        </FilterSortPanel>
      </FliesSection>
    </FliesLayout>
  );
}

/* ---------------------------------------------------------------- books */

export const BOOK_HELP: CardHelpContent = {
  title: "Books",
  body: (
    <>
      <HelpSection title="A book">
        <p>One arm's book on one day: its floor, the band it profits in, the cash at entry and exit, and the net after fees.</p>
      </HelpSection>
      <HelpSection title="Settle n/r">
        <p>The fee total: its settlement share was not recorded. The settle column reads n/r there rather than a zero.</p>
      </HelpSection>
      <HelpSection title="Slippage">
        <p>
          Recorded on held positions from 2026-09-25 on: what a paper fill's model concedes and, on a live fill, what the entry conceded
          against the mid it was asked from. It is inside gross and never subtracted again; n/r where any position behind a book did not
          record it.
        </p>
      </HelpSection>
      <HelpSection title="Filter and sort">
        <p>The reader pages newest first, so a sort orders the page in view.</p>
      </HelpSection>
    </>
  ),
};

export const BOOK_SORTS: SortOption<FliesBookRow>[] = [
  { id: "date", label: "date", hint: "newest first", key: (b) => -Date.parse(`${b.tradeDate}T00:00:00Z`) },
  { id: "net", label: "net", hint: "worst first", key: (b) => (b.status === "settled" ? b.pnl : null) },
  { id: "arm", label: "arm", hint: "A to Z", key: (b) => b.arm },
];

export function BooksCard({ data, paging, loading, isError, busy }: Props) {
  const rows = data?.books.rows ?? [];
  const total = data?.books.total ?? 0;
  const [sortChoice, setSortChoice] = useSortChoice("flies-books", "date");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const option = BOOK_SORTS.find((o) => o.id === sortChoice.id);
  const listed = sortRows(rows, option, sortChoice.reversed);
  const changed = sortChoice.id !== "date" || sortChoice.reversed ? 1 : 0;
  const reset = () => setSortChoice({ id: "date", reversed: false });
  return (
    <FliesLayout>
      <FliesSection concept="hist" name="books" question="How did each arm's book end each day?">
        <Card title="books" help={BOOK_HELP} isError={isError} controls={<FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />}>
          <FilterSummary
            parts={[`by ${option?.label ?? sortChoice.id}${sortChoice.reversed ? ", reversed" : `, ${option?.hint ?? ""}`}`]}
            note={`${total.toLocaleString()} books`}
            changed={changed}
            onOpen={() => setPanel(true)}
            onReset={reset}
          />
          <div className={`table-scroll ${busy ? "table-busy" : ""}`}>
            <table className="data-table num-from-1">
              <thead>
                <tr>
                  {["date", "arm", "sym", "status", "floor", "band", "entry", "exit", "gross", "fees", "settle", "slip", "net"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <SkeletonRows n={6} cols={13} />
                ) : listed.length === 0 ? (
                  <tr>
                    <td colSpan={13} className="muted">
                      no books
                    </td>
                  </tr>
                ) : (
                  listed.map((b) => (
                    <tr key={b.bookId}>
                      <td>{b.tradeDate}</td>
                      <td className="muted">{b.arm ?? "—"}</td>
                      <td>{b.symbol}</td>
                      <td>{b.status}</td>
                      <td>{b.floorHolds === null ? "—" : b.floorHolds ? "holds" : "no"}</td>
                      <td className="muted">{b.bandLow !== null && b.bandHigh !== null ? `${fmtNum(b.bandLow, 0)}–${fmtNum(b.bandHigh, 0)}` : "—"}</td>
                      <td>{fmtCash(b.entryCash)}</td>
                      <td>{b.status === "settled" ? fmtCash(b.exitCash) : "—"}</td>
                      <td>{b.status === "settled" ? fmtMoney(b.gross) : "—"}</td>
                      <td className="muted">{fmtMoney(b.fees)}</td>
                      <td className="muted">{b.settlementFees === null ? (b.status === "settled" ? "n/r" : "—") : fmtMoney(b.settlementFees)}</td>
                      <td className="muted">{b.slippage === null ? (b.status === "settled" ? "n/r" : "—") : fmtMoney(b.slippage)}</td>
                      <td>{b.status === "settled" ? <PnlCell v={b.pnl} /> : <span className="muted">—</span>}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {total > 0 && (
            <div className="card-footer">
              <Pager offset={data?.books.offset ?? paging.page.offset} limit={data?.books.limit ?? paging.page.limit} total={total} onOffset={paging.setOffset} onLimit={paging.setLimit} />
            </div>
          )}
        </Card>
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          <SortSection sorts={BOOK_SORTS} choice={sortChoice} fallback="date" onChange={setSortChoice} />
        </FilterSortPanel>
      </FliesSection>
    </FliesLayout>
  );
}
