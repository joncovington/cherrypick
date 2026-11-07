import type { ValuationRow } from "@console/shared";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, SortSection, sortRows, useSortChoice, type SortOption } from "../../components/FilterSort";
import { Pager, usePage } from "../../components/ScopeBar";
import { useListView } from "../../components/ViewSwitch";
import { useValuation } from "../../lib/api";
import { BoxOpenButton, ScreenerCard, ScreenerLayout, ScreenerSection, SessionSelect, SymbolSearch, Tabs, useParamState, useScreenerBoxes, useViewChoiceOr, type ScreenerBox } from "./ScreenerParts";
import { Dial, ValuationDetail, flagText, money, needsGrowth, pct, stateLabel, stretch, stretchText, type DetailSection } from "./ValuationDetail";

export { ValuationDetail, stretch } from "./ValuationDetail";

/**
 * The fair-value tab (2026-10-27): one DCF estimate an options-tradable name a session, from
 * `packages/technicals` `valuation` (`cherrypick.core.valuation` on Dolt's annual statements). Every
 * figure is the builder's; this formats and filters. It never orders by the gap (tested, no edge): by
 * default the rows open by how much the price asks of the company (`riskOrder`: reach, then `stretch`,
 * 2026-10-28, the user), the read for a PMCC long leg and for the stock a wheel or skate put may be
 * assigned; any other sort is the reader's own choice.
 *
 * Redesign (2026-11-06): each name is a row (cards on request, with the stretch dial) carrying only
 * what that choice needs (symbol, price, fair value, reach, stretch); the whole DCF detail opens as a
 * lightbox with tabs (overview, forecast, value per share, every input). The valued / pmcc / all view
 * stays as tabs; the sort and the symbol search sit behind filter & sort. "Tested 2018-26: no edge" is
 * the amber flag, the method behind the ?. pmcc's configured stocks carry a badge (its funds read not
 * applicable: a DCF on operating cash flow does not describe a fund, a bank or a REIT).
 */

const PAGE_SIZES = [24, 48, 96, 192] as const;
const DASH = "—";

const REACH_RANK: Record<string, number> = { "below value": 0, "within history": 1, "beyond history": 2 };

/** The default order: reach (below value, within history, beyond history), then stretch ascending,
 *  then symbol; a row with neither sinks. It orders by what the price asks, never by the gap. */
export function riskOrder(rows: ValuationRow[]): ValuationRow[] {
  const rank = (r: ValuationRow) => (r.reach === null ? 3 : (REACH_RANK[r.reach] ?? 3));
  return [...rows].sort((a, b) => {
    const byReach = rank(a) - rank(b);
    if (byReach !== 0) return byReach;
    const sa = stretch(a);
    const sb = stretch(b);
    if (sa !== sb) {
      if (sa === null) return 1;
      if (sb === null) return -1;
      return sa < sb ? -1 : 1;
    }
    return a.symbol.localeCompare(b.symbol);
  });
}

const REACH_TITLE: Record<string, string> = {
  "below value": "The price is at or under the model's value: it asks nothing the business has not already done",
  "within history":
    "The margin or the growth the price needs, either one alone, is no more than the company's best of the last five fiscal years",
  "beyond history":
    "Neither the margin nor the growth alone reaches the price without beating the company's own best of the last five fiscal years: the price assumes a better business than the record shows",
};

export type ValuationView = "valued" | "pmcc" | "all";

export function applyView(rows: ValuationRow[], view: ValuationView, q: string): ValuationRow[] {
  return rows.filter((r) => {
    if (q !== "" && !r.symbol.includes(q)) return false;
    if (view === "valued") return r.state === "valued";
    if (view === "pmcc") return r.pmcc;
    return true;
  });
}

/** The sorts: what the price asks (reach, then stretch) by default; name; stretch alone. Never the gap. */
export const VALUE_SORTS: SortOption<ValuationRow>[] = [
  { id: "risk", label: "risk order", hint: "reach, then stretch", key: () => 0 },
  { id: "symbol", label: "symbol", hint: "A to Z", key: (r) => r.symbol },
  {
    id: "stretch",
    label: "stretch",
    hint: "most first",
    key: (r) => {
      const s = stretch(r);
      return s === null ? null : s === Number.POSITIVE_INFINITY ? -Number.MAX_VALUE : -s;
    },
  },
];

/** A stretch as a share of the meter: 2× fills it. */
function stretchFill(s: number | null): number {
  return s === null ? 0 : Math.max(0, Math.min(100, (s / 2) * 100));
}

function Meter({ s }: { s: number | null }) {
  return (
    <span className="pos-bar vr-meter" title="stretch: the margin the price needs over the company's best of five years; the bar fills at 2×">
      <span className={`pos-bar-fill${s !== null && s > 1 ? " vr-warn" : ""}`} style={{ width: `${stretchFill(s)}%` }} />
    </span>
  );
}

function Badges({ r }: { r: ValuationRow }) {
  return (
    <>
      {r.type && r.type !== "Stock" ? <span className="muted"> · {r.type === "Exchange Traded Fund" ? "fund" : r.type}</span> : null}
      {r.pmcc ? (
        <span className="chip" title="A symbol pmcc trades (its config)">
          pmcc
        </span>
      ) : null}
      {r.flags.length > 0 ? (
        <span className="chip muted" title={r.flags.map(flagText).join(" ")}>
          {r.flags.length === 1 ? "1 note" : `${r.flags.length} notes`}
        </span>
      ) : null}
    </>
  );
}

function reachText(r: ValuationRow): string {
  if (r.state !== "valued") return `${stateLabel(r.state)}${r.reason ? ` · ${r.reason.replace(/_/g, " ")}` : ""}`;
  return r.reach ?? DASH;
}

/** One name as a row: symbol, price and fair value, reach (and the growth the price needs), stretch as a meter. */
function ValueRow({ r, onOpen }: { r: ValuationRow; onOpen: () => void }) {
  const s = stretch(r);
  return (
    <button type="button" className="vr-row" onClick={onOpen} aria-label={`${r.symbol}: open the model`}>
      <span className="vr-sym">
        {r.symbol}
        <Badges r={r} />
      </span>
      <span className="vr-px">
        {r.spot === null ? DASH : `$${money(r.spot)}`} <span className="muted">· fair {r.fairValue === null ? DASH : `$${money(r.fairValue)}`}</span>
      </span>
      <span className={r.state === "valued" && r.reach !== "beyond history" ? "vr-reach muted" : "vr-reach"} title={r.state === "valued" && r.reach ? REACH_TITLE[r.reach] : (r.reason ?? undefined)}>
        {reachText(r)}
        {r.state === "valued" && r.reach !== "below value" ? <span className="muted"> · needs {needsGrowth(r)}/yr</span> : null}
      </span>
      <span className="vr-stretch">
        {r.state === "valued" ? <Meter s={s} /> : null}
        <span className="muted vr-xs">{r.state === "valued" ? `stretch ${stretchText(s)}` : ""}</span>
      </span>
    </button>
  );
}

/** One name as a card: the price and fair value, the reach, the stretch dial and the button for the model. */
function ValueCard({ r, onOpen }: { r: ValuationRow; onOpen: () => void }) {
  const s = stretch(r);
  return (
    <article className="pos-card tc-card vr-card" aria-label={`${r.symbol}: fair value`}>
      <span className="pos-card-head">
        <span className="pos-card-titles">
          <span className="pos-card-sym-line">
            <span className="pos-card-sym">{r.symbol}</span>
            {r.spot === null ? null : <span className="pos-card-spot">{money(r.spot)}</span>}
          </span>
          <span className="pos-card-shape">fair value {r.fairValue === null ? DASH : `$${money(r.fairValue)}`}</span>
        </span>
        <span className="pos-card-chips">
          <Badges r={r} />
        </span>
      </span>
      {r.state === "valued" ? (
        <Dial
          value={s === null || s === Number.POSITIVE_INFINITY ? (s === null ? null : 2) : s}
          min={0}
          max={2}
          zones={[
            { from: 0, to: 1, color: "var(--pos)" },
            { from: 1, to: 2, color: "var(--warn)" },
          ]}
          ticks={[
            { at: 0, text: "0×" },
            { at: 1, text: "1×" },
            { at: 2, text: "2×" },
          ]}
          label={`stretch ${stretchText(s)}`}
        />
      ) : null}
      <span className="pos-chain-label">{reachText(r)}</span>
      <span className="pos-card-foot">
        <span>{r.state === "valued" ? `stretch ${stretchText(s)}` : "no stretch"}</span>
        <span className="pos-card-reason">{r.state === "valued" && r.reach !== "below value" ? `needs ${needsGrowth(r)}/yr` : ""}</span>
      </span>
      <div className="tc-extra">
        <BoxOpenButton onOpen={onOpen}>{r.symbol} · the model</BoxOpenButton>
      </div>
    </article>
  );
}

/** The names, as rows or cards by the card's view; a click on either opens the model. */
function ValueList({ rows, open }: { rows: ValuationRow[]; open: (id: string) => void }) {
  const view = useListView();
  if (view === "rows") {
    return (
      <div className="vr-list" role="list">
        {rows.map((r) => (
          <div key={r.symbol} role="listitem">
            <ValueRow r={r} onOpen={() => open(r.symbol)} />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="tc-grid">
      {rows.map((r) => (
        <ValueCard key={r.symbol} r={r} onOpen={() => open(r.symbol)} />
      ))}
    </div>
  );
}

export function valueHelp(universe: string | null | undefined, terminal: number | undefined): CardHelpContent {
  return {
    title: "Reading fair value",
    body: (
      <>
        <HelpWarn title="Tested 2018–26: no edge">
          <p>
            A model's estimate of each share's value from the company's own statements. Cheap names did no better than rich ones, so it is a
            description, never a reason to pick a name. What it is for: how much has to go right for today's price to hold, for a PMCC long
            leg or a stock a wheel or skate put may be assigned.
          </p>
        </HelpWarn>
        <HelpSection title="Reach and stretch">
          <p>
            <strong>Reach</strong> says whether the business has ever done what the price needs: below value (nothing new), within history (its
            best year would do), beyond history (better than it has ever done). <strong>Stretch</strong> is the margin the price needs over the
            company's best: under 1× is inside its record, over 1× is not. The list opens below value first, then within history, then
            beyond, each least stretched first. Open a name for the model.
          </p>
        </HelpSection>
        <HelpSection title="How the value is made">
          <p>
            Five forecast years from the company's last five fiscal years (growth from the analysts' next-year sales where there is one, faded
            to {pct(terminal ?? 0.02, 0)}), discounted at a cost of capital from the stock's beta, with a perpetuity after. The names are the
            security list's optionable names that the liquidity verdict calls options-tradable{universe ? ` (${universe})` : ""}. Statements
            are Dolt's annual rows, counted 45 days after the fiscal year ends; the broker's beta and market cap ride along as a cross-check
            and decide nothing.
          </p>
        </HelpSection>
      </>
    ),
  };
}

const TABS: DetailSection[] = ["overview", "forecast", "value", "inputs"];
const TAB_LABEL: Record<DetailSection, string> = { overview: "overview", forecast: "forecast", value: "value per share", inputs: "every input" };

export function ValuationTab() {
  const { params, set, clear } = useParamState();
  const day = params.get("day");
  const { data, isLoading, isError } = useValuation(day);
  const [sort, setSort] = useSortChoice("valuation", "risk");
  // Rows by default: a long list scanned by name (2026-11-06, the redesign's call).
  const [view, setView] = useViewChoiceOr("valuation", "rows");
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const viewParam = params.get("view");
  const list: ValuationView = viewParam === "all" || viewParam === "pmcc" ? viewParam : "valued";

  const all = data?.rows ?? [];
  const shown = applyView(all, list, q);
  const option = VALUE_SORTS.find((s) => s.id === sort.id);
  const ordered = sort.id === "risk" ? riskOrder(shown) : sortRows(shown, option, sort.reversed);
  const sorted = sort.id === "risk" && sort.reversed ? [...ordered].reverse() : ordered;
  const { page, setOffset, setLimit } = usePage([data?.day, list, q, sort.id, sort.reversed], 48);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const latest = data?.days[0] ?? null;
  const valued = all.filter((r) => r.state === "valued").length;
  const pmcc = all.filter((r) => r.pmcc).length;

  // The lightbox: every shown name's model, in the list's order, so ← → walk it.
  const boxes: ScreenerBox[] = sorted.map((r) => ({
    id: r.symbol,
    kicker: "fair value ›",
    title: r.state === "valued" && r.fairValue !== null ? `${r.symbol} · fair value $${money(r.fairValue)}` : `${r.symbol} · no fair value`,
    tabs: r.state === "valued" ? TABS.map((t) => ({ id: t, label: TAB_LABEL[t] })) : [{ id: "overview", label: "overview" }],
    body: (tab) => <ValuationDetail r={r} rule={data?.rule} section={r.state === "valued" ? (tab as DetailSection) : undefined} />,
  }));
  const box = useScreenerBoxes(boxes);

  const changed = (sort.id !== "risk" || sort.reversed ? 1 : 0) + (q !== "" ? 1 : 0);
  const reset = () => {
    void setSort({ id: "risk", reversed: false });
    clear(["q"]);
  };
  const sortLabel = VALUE_SORTS.find((s) => s.id === sort.id)?.label ?? sort.id;

  return (
    <ScreenerLayout>
      <ScreenerSection concept="hist" tag="valuation" question="How much does the price ask of the business?">
        <ScreenerCard
          title="fair value"
          help={valueHelp(data?.universe, data?.rule.terminalGrowth ?? undefined)}
          flag="no edge"
          extra={data ? <SessionSelect days={data.days} day={data.day} latest={latest} label="Session" onChange={(d) => set("day", d)} /> : null}
          tabs={
            <Tabs
              label="Names"
              value={list}
              options={[
                { id: "valued", label: `valued · ${valued}`, title: "Only the names the model valued" },
                { id: "pmcc", label: `pmcc · ${pmcc}`, title: "pmcc's configured symbols, valued or not (its funds read not applicable)" },
                { id: "all", label: `all · ${all.length}`, title: "Every name on the list, with why the model has no value for it" },
              ]}
              onChange={(v) => set("view", v === "valued" ? null : v)}
            />
          }
          view={{ value: view, onChange: setView }}
          filter={{
            changed,
            parts: [list, `by ${sortLabel}${sort.reversed ? ", reversed" : ""}`, ...(q ? [`“${q}”`] : [])],
            note: `${shown.length} of ${all.length}`,
            onReset: reset,
            panel: (
              <>
                <SortSection sorts={VALUE_SORTS} choice={sort} fallback="risk" onChange={setSort} />
                <FilterGroup title="symbol">
                  <SymbolSearch value={params.get("q") ?? ""} onChange={(v) => set("q", v === "" ? null : v)} />
                </FilterGroup>
              </>
            ),
          }}
        >
          {data?.day && (
            <p className="muted pf-caption" style={{ marginTop: 0 }}>
              session {data.day}
            </p>
          )}
          {isError && <p className="muted">Could not read the fair-value file.</p>}
          {isLoading && <p className="muted">Reading the fair-value file…</p>}
          {data && data.day === null && (
            <p className="muted">
              No fair-value file built yet: it is built at 06:28 ET from the history store, after the statements landing
              (technicals.history).
            </p>
          )}
          {rows.length > 0 && <ValueList rows={rows} open={box.open} />}
          {shown.length > PAGE_SIZES[0] && (
            <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
          )}
          {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}
        </ScreenerCard>
      </ScreenerSection>
      {box.node}
    </ScreenerLayout>
  );
}
