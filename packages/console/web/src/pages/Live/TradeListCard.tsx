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

/**
 * A list of held trades as a card of its own (2026-11-07): the trades as cards or rows (the icon
 * switch), the sort behind one button with its summary line, and `controls` (an arm filter) beside
 * them. Sorts are the caller's and are on size, timing and fit only. The modules with a paper book
 * and no live-trade page (bwb, calendars) lay their open positions out with this.
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
}: {
  /** The card's preference key: the view and the sort are remembered under it. */
  card: string;
  title: ReactNode;
  help?: CardHelpContent;
  rows: T[];
  model: (row: T) => TradeCardModel;
  sorts: SortOption<T>[];
  fallback: string;
  empty: string;
  controls?: ReactNode;
  updatedAt?: number;
  defaultView?: ListView;
  isError?: boolean;
  loading?: boolean;
}) {
  const [view, setView] = useViewChoice(card, defaultView);
  const [choice, setChoice] = useSortChoice(card, fallback);
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const option = sorts.find((o) => o.id === choice.id);
  const listed = sortRows(rows, option, choice.reversed);
  const changed = choice.id !== fallback || choice.reversed ? 1 : 0;
  const reset = () => setChoice({ id: fallback, reversed: false });
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
          parts={[`by ${option?.label ?? choice.id}${choice.reversed ? ", reversed" : `, ${option?.hint ?? ""}`}`]}
          note={`${listed.length.toLocaleString()} listed`}
          changed={changed}
          onOpen={() => setPanel(true)}
          onReset={reset}
        />
        {loading ? (
          <p className="muted">reading…</p>
        ) : (
          <ListViewContext.Provider value={view}>
            <TradeCards models={listed.map(model)} empty={empty} />
          </ListViewContext.Provider>
        )}
      </Card>
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <SortSection sorts={sorts} choice={choice} fallback={fallback} onChange={setChoice} />
      </FilterSortPanel>
    </>
  );
}
