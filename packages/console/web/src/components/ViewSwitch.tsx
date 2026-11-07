import { createContext, useContext } from "react";
import { getPref, usePrefsVersion, writePref } from "../lib/prefs";

/**
 * Cards or rows (2026-11-06): a parent card that lists trades can show each one in full, as a card,
 * or as one line a trade to scan many, a row opening its card in place. The switch is two icons in the
 * card head, remembered per card (`view.<card>`). A list reads the choice from `ListViewContext`, so a
 * card wraps its lists once and every list in it follows; outside a provider lists draw as cards.
 */

export type ListView = "cards" | "rows";

export const ListViewContext = createContext<ListView>("cards");

export function useListView(): ListView {
  return useContext(ListViewContext);
}

/** A card's remembered view; `fallback` (cards unless the card says rows) until one is chosen. */
export function useViewChoice(card: string, fallback: ListView = "cards"): [ListView, (v: ListView) => void] {
  usePrefsVersion();
  const raw = getPref(`view.${card}`);
  const v: ListView = raw === "rows" || raw === "cards" ? raw : fallback;
  return [v, (next) => void writePref(`view.${card}`, next)];
}

/** The two icons, in the toggle track. */
export function ViewSwitch({ view, onChange }: { view: ListView; onChange: (v: ListView) => void }) {
  return (
    <span className="mode-toggle view-switch" role="group" aria-label="view">
      <button
        type="button"
        className={view === "cards" ? "mode-btn active" : "mode-btn"}
        aria-pressed={view === "cards"}
        aria-label="Cards"
        title="Cards: each trade in full"
        onClick={() => onChange("cards")}
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <rect x="1.75" y="1.75" width="5.5" height="5.5" rx="1.2" />
          <rect x="8.75" y="1.75" width="5.5" height="5.5" rx="1.2" />
          <rect x="1.75" y="8.75" width="5.5" height="5.5" rx="1.2" />
          <rect x="8.75" y="8.75" width="5.5" height="5.5" rx="1.2" />
        </svg>
      </button>
      <button
        type="button"
        className={view === "rows" ? "mode-btn active" : "mode-btn"}
        aria-pressed={view === "rows"}
        aria-label="Rows"
        title="Rows: one line a trade; open one for its card"
        onClick={() => onChange("rows")}
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M2 3.5h12M2 8h12M2 12.5h12" />
        </svg>
      </button>
    </span>
  );
}
