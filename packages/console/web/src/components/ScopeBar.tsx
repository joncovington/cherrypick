import { useRef, useState } from "react";
import { FIRST_PAGE, PAGE_SIZES, type PageState } from "../lib/api";

/** Page-wide scope selectors — the shape every module dashboard leads with. */
export function ScopeSelect({
  label,
  value,
  options,
  onChange,
  allLabel = "all",
}: {
  label: string;
  value: string | null;
  options: string[] | undefined;
  onChange: (v: string | null) => void;
  allLabel?: string;
}) {
  return (
    <select
      className="text-input"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      aria-label={label}
      title={label}
    >
      <option value="">{allLabel}</option>
      {options?.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

/**
 * Era selector. Unlike the other scope selects, its default is not "everything"
 * — it is the module's current era, matching what the module's own analytics
 * count as evidence. Earlier eras stay reachable, and picking one says so out
 * loud rather than quietly mixing shakedown rows into the numbers.
 */
export function EraSelect({
  value,
  eras,
  currentEra,
  onChange,
}: {
  value: string | null;
  eras: Array<{ era: string; trades: number }> | undefined;
  currentEra: string | undefined;
  onChange: (v: string | null) => void;
}) {
  if (eras === undefined || eras.length < 2) return null;
  const total = eras.reduce((s, e) => s + e.trades, 0);
  const currentCount = eras.find((e) => e.era === currentEra)?.trades ?? 0;
  return (
    <select
      className={`text-input ${value === null ? "" : "scope-select-off-default"}`}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      aria-label="era"
      title="which era of trades counts as evidence"
    >
      <option value="">era {currentEra} · {currentCount}</option>
      {eras
        .filter((e) => e.era !== currentEra)
        .map((e) => (
          <option key={e.era} value={e.era}>
            era {e.era} · {e.trades}
          </option>
        ))}
      <option value="ALL">every era · {total}</option>
    </select>
  );
}

/** A tab strip in the page title row, styled like the mode toggle. */
export function TabStrip<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: readonly T[];
  value: T;
  onChange: (t: T) => void;
}) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      {tabs.map((t) => (
        <button key={t} type="button" className={value === t ? "mode-btn active" : "mode-btn"} onClick={() => onChange(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

/**
 * One table's page state, with the reset every filter change needs: an offset
 * into a result set that no longer exists points nowhere.
 */
export function usePage(resetKeys: unknown[] = []): {
  page: PageState;
  setOffset: (v: number) => void;
  setLimit: (v: number) => void;
} {
  const [page, setPage] = useState<PageState>(FIRST_PAGE);
  const key = JSON.stringify(resetKeys);
  const seen = useRef(key);
  if (seen.current !== key) {
    seen.current = key;
    if (page.offset !== 0) setPage((p) => ({ ...p, offset: 0 }));
  }
  return {
    page,
    setOffset: (offset: number) => setPage((p) => ({ ...p, offset })),
    setLimit: (limit: number) => setPage((p) => ({ limit, offset: Math.floor(p.offset / limit) * limit })),
  };
}

/**
 * Pager for a server-paged table. Reports the window against the true match
 * count, so the reader always knows how much sits outside the page — a table
 * that quietly stops at its page size is the thing this exists to prevent.
 */
export function Pager({
  offset,
  limit,
  total,
  pageSizes = PAGE_SIZES,
  onOffset,
  onLimit,
}: {
  offset: number;
  limit: number;
  total: number;
  pageSizes?: readonly number[];
  onOffset: (v: number) => void;
  onLimit: (v: number) => void;
}) {
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);
  const atStart = offset <= 0;
  const atEnd = last >= total;
  return (
    <div className="pager">
      <button type="button" className="pager-btn" disabled={atStart} onClick={() => onOffset(0)} title="first page">
        ⏮
      </button>
      <button
        type="button"
        className="pager-btn"
        disabled={atStart}
        onClick={() => onOffset(Math.max(0, offset - limit))}
        title="previous page"
      >
        ◀
      </button>
      <span className="pager-range">
        {first.toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}
      </span>
      <button
        type="button"
        className="pager-btn"
        disabled={atEnd}
        onClick={() => onOffset(offset + limit)}
        title="next page"
      >
        ▶
      </button>
      <button
        type="button"
        className="pager-btn"
        disabled={atEnd}
        onClick={() => onOffset(Math.max(0, (Math.ceil(total / limit) - 1) * limit))}
        title="last page"
      >
        ⏭
      </button>
      <select
        className="text-input pager-size"
        value={limit}
        onChange={(e) => {
          // Keep the first visible row visible across a page-size change.
          const next = Number(e.target.value);
          onLimit(next);
          onOffset(Math.floor(offset / next) * next);
        }}
        aria-label="rows per page"
        title="rows per page"
      >
        {pageSizes.map((n) => (
          <option key={n} value={n}>
            {n} / page
          </option>
        ))}
      </select>
    </div>
  );
}

/** Loop freshness pill: LIVE when the module's loop wrote within its window. */
export function LoopPill({
  state,
  ageSeconds,
  detail,
}: {
  state: "live" | "idle" | "no-data" | undefined;
  ageSeconds: number | null | undefined;
  detail?: string;
}) {
  if (state === undefined) return null;
  const age =
    ageSeconds == null
      ? ""
      : ageSeconds < 90
        ? ` · ${Math.round(ageSeconds)}s ago`
        : ageSeconds < 5400
          ? ` · ${Math.round(ageSeconds / 60)}m ago`
          : ` · ${(ageSeconds / 3600).toFixed(1)}h ago`;
  const cls = state === "live" ? "chip-ok" : state === "idle" ? "chip-warn" : "chip-missing";
  return (
    <span className={`chip ${cls}`} title={detail}>
      {state === "live" ? "● loop live" : state === "idle" ? "◐ loop idle" : "○ no loop data"}
      {age}
    </span>
  );
}
