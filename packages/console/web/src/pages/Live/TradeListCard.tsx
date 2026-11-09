import { useRef, useState, type ReactNode } from "react";
import { Card } from "../../components/DataTable";
import type { CardHelpContent } from "../../components/CardHelp";
import { ListViewContext, ViewSwitch, useViewChoice, type ListView } from "../../components/ViewSwitch";
import {
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { TradeCards, type TradeCardModel } from "./TradeCard";

/** A list's own filter beside the sort: which rows it keeps, its words on the summary line, how many
 *  of its choices are off their defaults, and its controls in the panel. */
export interface TradeListFilter<T> {
  keep: (row: T) => boolean;
  parts: string[];
  changed: number;
  reset: () => void;
  panel: ReactNode;
}

/**
 * A list of held trades as a card of its own (2026-11-07): the trades as cards or rows (the icon
 * switch), the sort behind one button with its summary line, and `controls` (an arm filter) beside
 * them. Sorts are the caller's and are on size, timing and fit only. bwb, calendars and flies lay
 * their positions out with this; a list that filters or pages adds `filter`, `note` and `footer`.
 */
export function TradeListCard<T>({
  card,
  title,
  help,
  rows,
  model,
  sorts,
  fallback,
  empty,
  controls,
  updatedAt,
  defaultView = "cards",
  isError = false,
  loading = false,
  filter,
  note,
  footer,
}: {
  /** The card's preference key: the view and the sort are remembered under it. */
  card: string;
  title: ReactNode;
  help?: CardHelpContent;
  rows: T[];
  model: (row: T) => TradeCardModel;
  sorts: SortOption<T>[];
  fallback: string;
  /** What an empty list says; a function gets how many rows there were before the filter. */
  empty: string | ((before: number) => string);
  controls?: ReactNode;
  updatedAt?: number;
  defaultView?: ListView;
  isError?: boolean;
  loading?: boolean;
  filter?: TradeListFilter<T>;
  /** The summary line's count; by default "N listed". */
  note?: (listed: number) => string;
  /** Below the list, e.g. a pager. */
  footer?: ReactNode;
}) {
  const [view, setView] = useViewChoice(card, defaultView);
  const [choice, setChoice] = useSortChoice(card, fallback);
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const option = sorts.find((o) => o.id === choice.id);
  const kept = filter === undefined ? rows : rows.filter(filter.keep);
  const listed = sortRows(kept, option, choice.reversed);
  const changed = (choice.id !== fallback || choice.reversed ? 1 : 0) + (filter?.changed ?? 0);
  const reset = () => {
    setChoice({ id: fallback, reversed: false });
    filter?.reset();
  };
  return (
    <>
      <Card
        title={title}
        collapseKey={card}
        help={help}
        isError={isError}
        updatedAt={updatedAt}
        controls={
          <>
            {controls}
            <ViewSwitch view={view} onChange={setView} />
            <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />
          </>
        }
      >
        <FilterSummary
          parts={[`by ${option?.label ?? choice.id}${choice.reversed ? ", reversed" : `, ${option?.hint ?? ""}`}`, ...(filter?.parts ?? [])]}
          note={note === undefined ? `${listed.length.toLocaleString()} listed` : note(listed.length)}
          changed={changed}
          onOpen={() => setPanel(true)}
          onReset={reset}
        />
        {loading ? (
          <p className="muted">reading…</p>
        ) : (
          <ListViewContext.Provider value={view}>
            <TradeCards models={listed.map(model)} empty={typeof empty === "function" ? empty(rows.length) : empty} />
          </ListViewContext.Provider>
        )}
        {footer !== undefined && footer !== null && footer !== false && <div className="card-footer">{footer}</div>}
      </Card>
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <SortSection sorts={sorts} choice={choice} fallback={fallback} onChange={setChoice} />
        {filter?.panel}
      </FilterSortPanel>
    </>
  );
}
