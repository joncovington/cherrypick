import { useRef, useState, type ReactNode } from "react";
import { FilterSortButton, FilterSortPanel, FilterSummary } from "../components/FilterSort";

/**
 * A card's local selects behind filter & sort (2026-11-07 redesign): `button` goes in the card's
 * `controls`, `body` (the summary line and the panel) at the top of its body. `parts` are the summary
 * line's words, `changed` counts what is off its default, `reset` puts them back. The pages outside the
 * modules share it so each one wires the panel the same way.
 */
export function useFilterPanel({
  parts,
  note,
  changed,
  reset,
  children,
}: {
  parts: string[];
  note?: string | null;
  changed: number;
  reset: () => void;
  children: ReactNode;
}): { button: ReactNode; body: ReactNode } {
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  return {
    button: <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />,
    body: (
      <>
        <FilterSummary parts={parts} note={note} changed={changed} onOpen={() => setPanel(true)} onReset={reset} />
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          {children}
        </FilterSortPanel>
      </>
    ),
  };
}
