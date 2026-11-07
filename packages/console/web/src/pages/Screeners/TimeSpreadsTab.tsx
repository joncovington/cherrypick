import { useMemo, useState } from "react";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, FilterSwitch, SortSection, sortRows, useSortChoice, type SortOption } from "../../components/FilterSort";
import type { TimeSpreadsPayload } from "@console/shared";
import { useViewChoice } from "../../components/ViewSwitch";
import { bandText } from "../../components/TimeSpreadTable";
import { fmtCash } from "../../lib/format";
import { useTimeSpreads } from "../../lib/api";
import {
  applyFilters,
  groupByName,
  joinAccount,
  type Direction,
  type Right,
  type StructureKind,
  type TimeSpreadFilters,
  type TimeSpreadView,
} from "../../lib/timeSpreadView";
import { TradeCards, timeSpreadModel, type TradeCardModel } from "../Live/TradeCard";
import { PanelChoice, ScreenerCard, ScreenerLayout, ScreenerSection, SessionSelect, useParamState } from "./ScreenerParts";

/**
 * The time-spread scanner (docs/time-spread-scanner-plan.md): the day's calendars and diagonals on the
 * names whose front week is priced rich to the back month. Each card says whether the account's sizing
 * (the same day's manage evaluation) fits it, or which cap stops it. The filters live in the URL as the
 * other Screeners tabs' do. Every figure is the scan's or manage's; this formats and decides nothing,
 * and nothing here stages or sends an order.
 *
 * NO TESTED EDGE: rows are ordered by the front/back IV ratio, the hypothesis item 54 tests.
 *
 * Redesign (2026-11-06): each structure is the same trade card as adding trades on the portfolio tab,
 * with its management levels (the expanded row before) in the card. The structure, right, direction,
 * account and max-debit filters and the sort sit behind filter & sort; group by name is a view choice
 * beside cards / rows. The amber flag says no tested edge; the left-out and did-not-pass audit is in
 * the ?.
 */

const DEFAULT_SORT = "ratio";

function pick<T extends string>(v: string | null, allowed: readonly T[]): T | null {
  return allowed.find((a) => a === v) ?? null;
}

function pct0(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function filtersFromParams(p: URLSearchParams): TimeSpreadFilters {
  const raw = p.get("maxdb");
  const max = Number(raw);
  return {
    structure: pick<StructureKind>(p.get("structure"), ["calendar", "diagonal"]),
    right: pick<Right>(p.get("right"), ["put", "call"]),
    direction: pick<Direction>(p.get("direction"), ["bullish", "neutral", "bearish"]),
    maxDebit: raw !== null && raw !== "" && Number.isFinite(max) && max > 0 ? max : null,
    fitsOnly: p.get("fits") === "1",
  };
}

/** The sorts: the scan's own order (the front/back IV ratio, untested) by default, then name, timing,
 *  size. Never by the take-profit or theta: with no tested edge that would read as a ranking. */
export const SPREAD_VIEW_SORTS: SortOption<TimeSpreadView>[] = [
  { id: DEFAULT_SORT, label: "IV ratio (untested)", hint: "the scan's order", key: () => 0 },
  { id: "symbol", label: "symbol", hint: "A to Z", key: (v) => v.s.underlying },
  { id: "front", label: "front expiry", hint: "soonest first", key: (v) => v.s.front },
  { id: "debit", label: "debit", hint: "smallest first", key: (v) => v.s.debitMid },
  { id: "maxloss", label: "max loss", hint: "largest first", key: (v) => (v.s.maxLoss === null ? null : -Math.abs(v.s.maxLoss)) },
];

function chipsFor(v: TimeSpreadView) {
  return (
    <>
      {v.state === "fits" ? <span className="chip chip-ok">fits ×{v.units}</span> : null}
      {v.state === "stopped" ? (
        <span className="chip chip-warn" title="the cap that stops the first unit">
          {v.binding ?? "too big"}
        </span>
      ) : null}
      {v.state === "stopped-unlisted" ? (
        <span className="chip chip-warn" title="the evaluation stopped this row but itemises only some of them">
          too big
        </span>
      ) : null}
      {v.state === "absent" ? (
        <span className="chip" title="the evaluation holds time spreads but not this row">
          not sized
        </span>
      ) : null}
      {v.heldName ? <span className="chip">held name</span> : null}
      {!v.heldName && v.heldSector ? <span className="chip">sector held</span> : null}
    </>
  );
}

/**
 * A scan structure as the adding-trades card draws it, at the size the account's sizing fits (one
 * unit where it fits none), with the levels a person manages it by on the card's lines: take profit,
 * value stop, the break-even and price-stop bands. The bar is the max loss against the largest on the
 * list (the scan carries no per-trade cap).
 */
export function scanModel(v: TimeSpreadView, largest: number): TradeCardModel {
  const s = v.s;
  const m = timeSpreadModel({ ...s, units: v.state === "fits" ? v.units : null }, null);
  const n = v.state === "fits" && v.units ? v.units : 1;
  const maxLoss = s.maxLoss === null ? null : Math.abs(s.maxLoss) * n;
  return {
    ...m,
    chips: chipsFor(v),
    // The roll date is a level a person manages it by: the model's foot names it only where no earnings
    // date comes first.
    foot: [!s.earningsDate || !s.rollBy ? m.foot[0] : `${m.foot[0]} · roll by ${s.rollBy}`, m.foot[1]],
    faded: v.state === "stopped" || v.state === "stopped-unlisted",
    bar: maxLoss === null || largest <= 0 ? null : (maxLoss / largest) * 100,
    barTitle: "the debit at risk, against the largest on this list: the scan carries no per-trade cap",
    label: `max loss ${maxLoss === null ? "—" : `$${Math.round(maxLoss).toLocaleString("en-US")}`}${v.binding ? ` · stopped by ${v.binding}` : ""}`,
    after: [
      m.after[0] ?? "",
      `${m.after[1] ?? ""} · +5% ${fmtCash(s.pnlUp5 === null ? null : s.pnlUp5 * n)} · −10% ${fmtCash(s.pnlDown10 === null ? null : s.pnlDown10 * n)}${s.losesUnchanged ? " · loses flat" : ""}`,
      `take profit ${s.takeProfit === null ? "—" : `${s.takeProfit.toFixed(2)} cr`} · value stop ${s.valueStop === null ? "—" : `${s.valueStop.toFixed(2)} cr`}`,
      `break-even ${bandText(s.breakeven)} · price stop ${bandText(s.priceStop)} (close beyond it)`,
    ],
  };
}

/** The ? behind the card: no tested edge, where the structures come from, how the account sized them,
 *  and the audit of what was left out and which names did not pass. */
export function scanHelp(d: TimeSpreadsPayload): CardHelpContent {
  const dropped = Object.entries(d.dropped).map(([why, n]) => `${n} ${why}`);
  const failing = d.terms.filter((t) => !t.passes);
  const why: Record<string, number> = {};
  for (const t of failing) for (const w of t.why) why[w] = (why[w] ?? 0) + 1;
  const a = d.account;
  return {
    title: "Where these come from",
    body: (
      <>
        <HelpWarn title="No tested edge">
          <p>{d.order ?? "Ordered by the front/back IV ratio, an untested hypothesis"} (item 54).</p>
        </HelpWarn>
        <HelpSection title="Where they come from">
          <p>
            Calendars at the money and one strike either side, and put and call diagonals, on the {d.shortlist.length} best ratios of {d.passing ?? "—"} names
            that passed ({d.read ?? "—"} read of {d.universe ?? "—"} tradable). Modelled at mid from the {d.day} scan: re-quote before sending.
            {d.problem ? ` The scan is incomplete: ${d.problem}.` : ""}
          </p>
        </HelpSection>
        <HelpSection title="Sized to the account">
          <p>
            {a
              ? `Marked against the account's sizing${a.sourceSession ? ` (the ${a.sourceSession} evaluation)` : ""}: ${a.fitsTotal} fit, ${a.tooBigTotal} too big${
                  a.bpCapPct !== null ? `, buying-power cap ${pct0(a.bpCapPct)}` : ""
                }.`
              : `${d.accountNote ?? "no account sizing"}: cards are shown unmarked.`}{" "}
            Sized to the account on Live, portfolio. Nothing here is placed.
          </p>
        </HelpSection>
        {dropped.length > 0 ? (
          <HelpSection title="Left out">
            <ul>
              {dropped.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </HelpSection>
        ) : null}
        {Object.keys(why).length > 0 ? (
          <HelpSection title="Names that did not pass">
            <ul>
              {Object.entries(why).map(([w, n]) => (
                <li key={w}>
                  {n} {w}
                </li>
              ))}
            </ul>
          </HelpSection>
        ) : null}
      </>
    ),
  };
}

export function TimeSpreadsTab() {
  const { params, set, clear } = useParamState();
  const [day, setDay] = useState<string | null>(null);
  const [sort, setSort] = useSortChoice("time-spreads", DEFAULT_SORT);
  const [view, setView] = useViewChoice("time-spreads");
  const q = useTimeSpreads(day);
  const d = q.data;
  const views = useMemo(() => joinAccount(d?.structures ?? [], d?.account ?? null), [d]);
  if (!d) return <p className="muted">{q.isError ? "the time-spread scan could not be read" : "loading…"}</p>;
  const filters = filtersFromParams(params);
  const grouped = params.get("group") === "1";
  const shown = sortRows(applyFilters(views, filters), SPREAD_VIEW_SORTS.find((s) => s.id === sort.id), sort.reversed);
  const groups = grouped ? groupByName(shown) : [];
  const a = d.account;
  const largest = Math.max(0, ...views.map((v) => (v.s.maxLoss === null ? 0 : Math.abs(v.s.maxLoss) * (v.state === "fits" && v.units ? v.units : 1))));
  const model = (v: TimeSpreadView) => scanModel(v, largest);

  const changed =
    (sort.id !== DEFAULT_SORT || sort.reversed ? 1 : 0) +
    (filters.structure !== null ? 1 : 0) +
    (filters.right !== null ? 1 : 0) +
    (filters.direction !== null ? 1 : 0) +
    (filters.maxDebit !== null ? 1 : 0) +
    (filters.fitsOnly ? 1 : 0);
  const reset = () => {
    void setSort({ id: DEFAULT_SORT, reversed: false });
    clear(["structure", "right", "direction", "maxdb", "fits"]);
  };
  const sortLabel = SPREAD_VIEW_SORTS.find((s) => s.id === sort.id)?.label ?? sort.id;
  const help = scanHelp(d);

  return (
    <ScreenerLayout>
      <ScreenerSection concept="add" tag="structures" question="Which calendars and diagonals fit?">
        <ScreenerCard
          title="time spreads"
          help={help}
          flag="no tested edge"
          updatedAt={q.dataUpdatedAt}
          extra={
            d.days.length > 1 ? <SessionSelect days={d.days} day={d.day} latest={d.days[0] ?? null} label="scan day" onChange={(x) => setDay(x)} /> : null
          }
          tabs={
            <span className="mode-toggle" role="group" aria-label="grouping">
              <button
                type="button"
                className={grouped ? "mode-btn active" : "mode-btn"}
                aria-pressed={grouped}
                title="One block a name, with its ratio, IV rank, spot and earnings"
                onClick={() => set("group", grouped ? null : "1")}
              >
                by name
              </button>
            </span>
          }
          view={{ value: view, onChange: setView }}
          filter={{
            changed,
            parts: [
              ...(filters.fitsOnly ? ["fits only"] : []),
              ...(filters.structure ? [`${filters.structure}s`] : []),
              ...(filters.right ? [`${filters.right}s`] : []),
              ...(filters.direction ? [filters.direction] : []),
              ...(filters.maxDebit !== null ? [`debit ≤ ${filters.maxDebit.toFixed(2)}`] : []),
              `by ${sortLabel}${sort.reversed ? ", reversed" : ""}`,
            ],
            note: `${shown.length} of ${views.length}${a && a.sourceSession && a.sourceSession !== d.day ? ` · that evaluation sized the ${a.sourceSession} scan, so cards may not match` : ""}${a ? "" : " · unmarked: no account sizing"}`,
            onReset: reset,
            panel: (
              <>
                <SortSection sorts={SPREAD_VIEW_SORTS} choice={sort} fallback={DEFAULT_SORT} onChange={setSort} />
                <FilterGroup title="structure">
                  <PanelChoice
                    label="Structure"
                    value={filters.structure ?? "all"}
                    options={[
                      { id: "all", label: "both" },
                      { id: "calendar", label: "calendars" },
                      { id: "diagonal", label: "diagonals" },
                    ]}
                    onChange={(v) => set("structure", v === "all" ? null : v)}
                  />
                </FilterGroup>
                <FilterGroup title="right and direction">
                  <PanelChoice
                    label="Right"
                    value={filters.right ?? "all"}
                    options={[
                      { id: "all", label: "puts and calls" },
                      { id: "put", label: "puts" },
                      { id: "call", label: "calls" },
                    ]}
                    onChange={(v) => set("right", v === "all" ? null : v)}
                  />
                  <PanelChoice
                    label="Direction"
                    value={filters.direction ?? "all"}
                    options={[
                      { id: "all", label: "any" },
                      { id: "bullish", label: "bullish" },
                      { id: "neutral", label: "neutral" },
                      { id: "bearish", label: "bearish" },
                    ]}
                    onChange={(v) => set("direction", v === "all" ? null : v)}
                  />
                </FilterGroup>
                <FilterGroup title="account">
                  <FilterSwitch
                    label="Fits only"
                    description={a ? "Only structures the account's sizing fits." : "Needs the account's sizing, which is missing."}
                    on={filters.fitsOnly}
                    disabled={!a}
                    onChange={(on) => set("fits", on ? "1" : null)}
                  />
                  <label className="chip" title="Largest debit at the mid, per share">
                    max debit{" "}
                    <input
                      type="number"
                      min="0"
                      step="0.25"
                      aria-label="Maximum debit per share"
                      style={{ width: "5em" }}
                      value={params.get("maxdb") ?? ""}
                      onChange={(e) => set("maxdb", e.target.value === "" ? null : e.target.value)}
                    />
                  </label>
                </FilterGroup>
              </>
            ),
          }}
        >
          {d.error ? (
            <p className="muted">{d.error}</p>
          ) : d.structures.length === 0 ? (
            <p className="muted">no structure cleared its gates today</p>
          ) : shown.length === 0 ? (
            <p className="muted">no structure matches these filters</p>
          ) : grouped ? (
            groups.map((g) => (
              <details key={g.underlying} open style={{ marginBottom: 8 }}>
                <summary>
                  <strong>{g.underlying}</strong>{" "}
                  <span className="muted">
                    ratio ×{g.ratio?.toFixed(2) ?? "—"} · IV rank {g.ivRank === null ? "—" : pct0(g.ivRank)} · spot{" "}
                    {g.spot === null ? "—" : `$${g.spot.toFixed(2)}`} · earnings {g.earningsDate ?? "—"} ·{" "}
                    {a ? `${g.fitCount} of ${g.rows.length} fit` : `${g.rows.length} rows`}
                  </span>
                </summary>
                <TradeCards models={g.rows.map(model)} />
              </details>
            ))
          ) : (
            <TradeCards models={shown.map(model)} />
          )}
        </ScreenerCard>
      </ScreenerSection>
    </ScreenerLayout>
  );
}
