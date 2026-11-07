import { useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../../components/DataTable";
import { type CardHelpContent } from "../../components/CardHelp";
import { FilterSortButton, FilterSortPanel, FilterSummary } from "../../components/FilterSort";
import { ListViewContext, ViewSwitch, type ListView } from "../../components/ViewSwitch";
import { getPref, usePrefsVersion, writePref } from "../../lib/prefs";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";

/**
 * The Screeners pages' shared parts (2026-11-06 redesign): a section under the question it answers, a
 * card whose list sits behind the suite's filter & sort button with its summary line and its cards or
 * rows switch, and the lightbox a card opens for what is too wide for it. Each page keeps its own
 * filters and its own figures; this only places them, as the Live portfolio tab places its cards.
 */

/** The marks the sections wear: the portfolio tab's own classes, so the two read alike. */
export type Concept = "income" | "dir" | "add" | "hist";

/** A section under its question, in the portfolio tab's markup. */
export function ScreenerSection({ concept, tag, question, children }: { concept: Concept; tag: string; question: string; children: ReactNode }) {
  return (
    <section className={`pf-sec pf-c-${concept}`} aria-label={tag}>
      <div className="pf-sec-head">
        <span className="pf-tag">{tag}</span>
        <h3>{question}</h3>
      </div>
      <div className="pf-row">{children}</div>
    </section>
  );
}

/** The page's sections in the portfolio tab's container, so the cards fold with the width. */
export function ScreenerLayout({ children }: { children: ReactNode }) {
  return <div className="pf-layout sc-layout">{children}</div>;
}

export interface ScreenerFilter {
  /** How many of its choices are off their defaults: the badge on the button. */
  changed: number;
  /** What is on, in words: each part opens the panel. */
  parts: string[];
  /** What is hidden, in words ("26 shown · 6 under $25 hidden"). */
  note?: string | null;
  onReset: () => void;
  /** The panel's groups. */
  panel: ReactNode;
}

/**
 * One list card: title with its ? and amber flag, the visible tabs, the cards/rows switch (where the
 * list has one), the filter & sort button, the summary line, and the body. The body reads the view
 * from `ListViewContext`, so every TradeCards inside follows the switch.
 */
export function ScreenerCard({
  title,
  help,
  flag,
  tabs,
  extra,
  updatedAt,
  view,
  filter,
  children,
}: {
  title: string;
  help?: CardHelpContent;
  flag?: string;
  tabs?: ReactNode;
  /** More head controls, before the tabs (a session picker). */
  extra?: ReactNode;
  /** When the list was read (the card prints "as of"). */
  updatedAt?: number;
  view?: { value: ListView; onChange: (v: ListView) => void };
  filter?: ScreenerFilter;
  children: ReactNode;
}) {
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const reset = () => filter?.onReset();
  return (
    <Card
      title={title}
      help={help}
      flag={flag}
      updatedAt={updatedAt}
      controls={
        <>
          {extra}
          {tabs}
          {view ? <ViewSwitch view={view.value} onChange={view.onChange} /> : null}
          {filter ? <FilterSortButton changed={filter.changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} /> : null}
        </>
      }
    >
      <ListViewContext.Provider value={view?.value ?? "cards"}>
        {filter ? (
          <FilterSummary parts={filter.parts} note={filter.note} changed={filter.changed} onOpen={() => setPanel(true)} onReset={reset} />
        ) : null}
        {children}
      </ListViewContext.Provider>
      {filter ? (
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          {filter.panel}
        </FilterSortPanel>
      ) : null}
    </Card>
  );
}

/** A card's remembered cards-or-rows view, with the default a page asks for (rows for a long list
 *  scanned by name); the stored choice wins once one is made. The shared `useViewChoice` starts every
 *  list on cards. */
export function useViewChoiceOr(card: string, fallback: ListView): [ListView, (v: ListView) => void] {
  usePrefsVersion();
  const raw = getPref(`view.${card}`);
  const v: ListView = raw === "rows" || raw === "cards" ? raw : fallback;
  return [v, (next) => void writePref(`view.${card}`, next)];
}

/** A group of visible tabs: one active, the others a click. */
export function Tabs<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { id: T; label: string; title?: string }[]; onChange: (id: T) => void }) {
  return (
    <span className="mode-toggle" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" title={o.title} className={o.id === value ? "mode-btn active" : "mode-btn"} aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

/** Choices inside the panel: a short run of options, one on. */
export function PanelChoice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { id: T; label: string; title?: string }[]; onChange: (id: T) => void }) {
  return <Tabs label={label} value={value} options={options} onChange={onChange} />;
}

/** The symbol search, inside the panel. */
export function SymbolSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <input
      className="chip review-session-select"
      value={value}
      placeholder="symbol"
      aria-label="Filter by symbol"
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** The session picker, small, in the card head: the day the list was built. */
export function SessionSelect({ days, day, latest, label, onChange }: { days: string[]; day: string | null; latest: string | null; label: string; onChange: (day: string | null) => void }) {
  if (days.length === 0) return null;
  return (
    <select className="chip review-session-select" aria-label={label} value={day ?? ""} onChange={(e) => onChange(e.target.value === latest ? null : e.target.value)}>
      {days.map((d) => (
        <option key={d} value={d}>
          {d}
        </option>
      ))}
    </select>
  );
}

/** URL state for a page: read a value, set or clear one, clear several (a filter reset). */
export function useParamState() {
  const [params, setParams] = useSearchParams();
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const clear = (keys: string[]) => {
    const next = new URLSearchParams(params);
    for (const k of keys) next.delete(k);
    setParams(next, { replace: true });
  };
  return { params, set, clear };
}

/* ------------------------------------------------------------------------ the lightbox */

/** One lightbox a card can open: where it sits, its tabs, and what each shows. */
export interface ScreenerBox {
  id: string;
  kicker: string;
  title: string;
  chips?: ReactNode;
  tabs: LightboxTab[];
  body: (tab: string) => ReactNode;
}

/** The lightbox's URL param: `?sc=<id>`, so a link opens it and the back button closes it. */
export const BOX_PARAM = "sc";

function BoxDialog({ boxes, index, onStep, onClose }: { boxes: ScreenerBox[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const box = boxes[index]!;
  const [tabState, setTab] = useState<{ id: string; tab: string }>({ id: box.id, tab: box.tabs[0]?.id ?? "" });
  // A step to another box opens it on its first tab.
  const tab = tabState.id === box.id ? tabState.tab : (box.tabs[0]?.id ?? "");
  return (
    <CardLightbox count={boxes.length} index={index} onStep={onStep} onClose={onClose} wide>
      {(nav) => (
        <LightboxFrame
          kicker={box.kicker}
          title={box.title}
          chips={box.chips}
          tabs={box.tabs}
          tab={tab}
          onTab={(t) => setTab({ id: box.id, tab: t })}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          <div className="pf-box-body">{box.body(tab)}</div>
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/**
 * The lightboxes of a page, and the way to open one. `boxes` are every one the page can open; the
 * open one is named in the URL. `open(id)` is what a card's button calls.
 */
export function useScreenerBoxes(boxes: ScreenerBox[]): { open: (id: string) => void; has: (id: string) => boolean; node: ReactNode } {
  const lb = useOpenParam(BOX_PARAM);
  const index = boxes.findIndex((b) => b.id === lb.open);
  const ids = new Set(boxes.map((b) => b.id));
  return {
    open: lb.show,
    has: (id) => ids.has(id),
    node: index >= 0 ? <BoxDialog boxes={boxes} index={index} onStep={(to) => lb.step(boxes[to]?.id ?? "")} onClose={lb.close} /> : null,
  };
}

/** A card's button for a lightbox, in the portfolio tab's look. */
export function BoxOpenButton({ onOpen, children, label }: { onOpen: () => void; children: ReactNode; label?: string }) {
  return (
    <button type="button" className="pf-more" aria-label={label} onClick={onOpen}>
      {children} <span aria-hidden="true">›</span>
    </button>
  );
}
