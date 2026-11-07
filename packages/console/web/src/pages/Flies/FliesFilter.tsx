import { useRef, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArmSelect } from "../../components/ArmSelect";
import { EraSelect } from "../../components/ScopeBar";
import { HelpWarn } from "../../components/CardHelp";
import { FilterGroup, FilterSortButton, FilterSortPanel, FilterSummary } from "../../components/FilterSort";
import type { FliesMeta } from "../../lib/api";

/**
 * The flies module's one filter & sort (2026-11-06): what the page header held as four selects (arm,
 * symbol, era, date) sits behind one button, and a summary line under the header says what is on, so a
 * narrowed or pooled view is never invisible. The era caveat is a warning in the panel, where the
 * choice is made. The module's tabs share it: the header owns the state (it is the scope every tab
 * reads), a tab only reads it.
 *
 * This file also holds the small pieces the flies tabs share: the question section, a link to a
 * sibling tab that keeps the scope in the address, and a one-of-several choice for a card's panel.
 */

export interface FliesScope {
  arm: string | null;
  symbol: string | null;
  /** null is the module's current era; "ALL" pools every era, a stated choice. */
  era: string | null;
  /** null is the latest day. */
  date: string | null;
}

export const NO_SCOPE: FliesScope = { arm: null, symbol: null, era: null, date: null };

export const ERA_CAVEAT =
  "The XSP books (2026-07-29..07-31) are a different trade: 1-wide structures whose trading fees were 6.9% of premium collected against the SPX book's 3.7% (32% against 11% on the median completed fly). Pooling them distorts every per-arm breakdown.";

/** How many of the four are off their default: the count on the button. */
export function scopeChanged(s: FliesScope): number {
  return [s.arm, s.symbol, s.era, s.date].filter((v) => v !== null).length;
}

/** The summary line's parts, one per scope, in the words the panel uses. */
export function scopeParts(s: FliesScope, meta: Pick<FliesMeta, "eras" | "currentEra" | "dates"> | undefined): string[] {
  const parts = [s.arm ?? "all arms"];
  if (s.symbol !== null) parts.push(s.symbol);
  if (s.era === "ALL") parts.push("all eras, pooled");
  else if (s.era !== null) parts.push(meta?.eras.find((e) => e.era === s.era)?.label ?? `era ${s.era}`);
  else if ((meta?.eras.length ?? 0) > 1) parts.push(meta?.eras.find((e) => e.era === meta.currentEra)?.label ?? "current era");
  parts.push(s.date === null ? `latest day${meta?.dates[0] ? ` · ${meta.dates[0].slice(5)}` : ""}` : s.date);
  return parts;
}

/** A tab's question: a tag, the question, then a row of cards that folds to one column on a phone.
 *  The concept sets the tag's colour (`pf-c-*`); `name` is the tag's word. */
/** The sections of one tab, in the portfolio tab's layout. */
export function FliesLayout({ children }: { children: ReactNode }) {
  return <div className="pf-layout fl-layout">{children}</div>;
}

/** A button-styled link to another flies tab, carrying the scope that is in the address. */
export function TabLink({ to, children }: { to: string; children: ReactNode }) {
  const { search } = useLocation();
  return (
    <Link className="pf-more" to={`${to}${search}`}>
      {children} <span aria-hidden="true">›</span>
    </Link>
  );
}

/** One of several, as a track of buttons in a panel (the direction toggle's look). */
export function ChoiceGroup<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: ReadonlyArray<{ id: T; label: string }>; onChange: (v: T) => void }) {
  return (
    <span className="mode-toggle fs-dir fl-choice" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" className={value === o.id ? "mode-btn active" : "mode-btn"} aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

/** The header's button and its panel: arm, symbol, era (with its warning) and date. */
export function FliesScopeFilter({
  scope,
  meta,
  onChange,
  open,
  onOpenChange,
}: {
  scope: FliesScope;
  meta: FliesMeta | undefined;
  onChange: (next: FliesScope) => void;
  /** Held by the page, so the summary line's parts can open the same panel. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const setOpen = onOpenChange;
  const anchor = useRef<HTMLButtonElement>(null);
  const set = (patch: Partial<FliesScope>) => onChange({ ...scope, ...patch });
  return (
    <>
      <FilterSortButton changed={scopeChanged(scope)} open={open} onOpen={() => setOpen(!open)} anchor={anchor} />
      <FilterSortPanel open={open} onClose={() => setOpen(false)} onReset={() => onChange(NO_SCOPE)} anchor={anchor}>
        <FilterGroup title="arm">
          <div className="fl-field">
            <ArmSelect module="flies" value={scope.arm} options={meta?.arms} onChange={(v) => set({ arm: v })} />
          </div>
        </FilterGroup>
        {(meta?.symbols.length ?? 0) > 1 && (
          <FilterGroup title="symbol">
            <div className="fl-field">
              <select className="chip review-session-select" value={scope.symbol ?? ""} onChange={(e) => set({ symbol: e.target.value === "" ? null : e.target.value })} aria-label="symbol filter">
                <option value="">all symbols</option>
                {meta?.symbols.map((sym) => (
                  <option key={sym} value={sym}>
                    {sym}
                  </option>
                ))}
              </select>
            </div>
          </FilterGroup>
        )}
        {(meta?.eras.length ?? 0) > 1 && (
          <FilterGroup title="era">
            <div className="fl-field">
              <EraSelect value={scope.era} eras={meta?.eras} currentEra={meta?.currentEra} onChange={(v) => set({ era: v })} pooledLabel="all eras, pooled" title={ERA_CAVEAT} />
            </div>
            <HelpWarn title="Different trade">
              <p>{ERA_CAVEAT}</p>
            </HelpWarn>
          </FilterGroup>
        )}
        <FilterGroup title="date" note="the session the day tabs show">
          <div className="fl-field">
            <select className="chip review-session-select" value={scope.date ?? ""} onChange={(e) => set({ date: e.target.value === "" ? null : e.target.value })} aria-label="date filter">
              <option value="">latest day</option>
              {meta?.dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        </FilterGroup>
      </FilterSortPanel>
    </>
  );
}

/** The line under the header: what the tabs below are scoped to. A part opens the panel through `onOpen`. */
export function FliesScopeSummary({
  scope,
  meta,
  note,
  onOpen,
  onReset,
}: {
  scope: FliesScope;
  meta: FliesMeta | undefined;
  note?: string | null;
  onOpen: () => void;
  onReset: () => void;
}) {
  return <FilterSummary parts={scopeParts(scope, meta)} note={note} changed={scopeChanged(scope)} onOpen={onOpen} onReset={onReset} />;
}
