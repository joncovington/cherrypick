import { Link, useSearchParams } from "react-router-dom";
import type { ValuationRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useValuation } from "../../lib/api";

/**
 * The fair-value tab (2026-10-27): one DCF estimate a liquid optionable name a session, from
 * `packages/technicals` `valuation` (`cherrypick.core.valuation` on Dolt's annual statements). Every
 * figure is the builder's; this formats and filters, and ranks nothing -- rows are by symbol, and a
 * column sort is the reader's own choice. The standing note says the estimate was tested and has no edge
 * and is never removed while the table shows. pmcc's configured stocks carry a badge (its funds read
 * not applicable: a DCF on operating cash flow does not describe a fund, a bank or a REIT).
 */

const PAGE_SIZES = [50, 100, 200, 500] as const;
const DASH = "—";

const FLAG_TITLE: Record<string, string> = {
  terminal_heavy: "terminal value over 85% of EV: the answer is the perpetuity, not the forecast",
  wacc_floored: "the cost of capital came out under 7% and was floored there",
  split_adjusted: "the stock split after its last annual report; the shares were carried through the split",
  negative_base_fcf: "the first forecast year's cash flow is negative",
  beta_bounded: "the regression beta sat outside 0.5-2.0 and was bounded",
  beta_disagrees: "the broker's beta differs from ours by more than 0.5",
  shares_stale: "spot x year-end shares is more than 10% off the broker's market cap",
};

const STATE_LABEL: Record<string, string> = {
  valued: "valued",
  "not-applicable": "not applicable",
  "no-statements": "no statements",
  refused: "refused",
};

function money(v: number | null): string {
  return v === null ? DASH : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null, digits = 1): string {
  return v === null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v: number | null): string {
  if (v === null) return DASH;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
}

function billions(v: number | null): string {
  if (v === null) return DASH;
  const b = v / 1e9;
  return b >= 100 ? `$${b.toFixed(0)}B` : b >= 1 ? `$${b.toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;
}

const SORTS: Record<string, (r: ValuationRow) => number | string | null> = {
  symbol: (r) => r.symbol,
  price: (r) => r.spot,
  fair: (r) => r.fairValue,
  gap: (r) => r.gap,
  wacc: (r) => r.wacc,
  beta: (r) => r.beta,
  growth: (r) => r.growthY1,
  margin: (r) => r.ebitMargin,
  terminal: (r) => r.terminalShare,
  multiple: (r) => r.impliedEvEbitda,
  needsMargin: (r) => r.impliedMargin,
  needsGrowth: (r) => r.impliedGrowth,
  reach: (r) => r.reach,
};

const REACH_TITLE: Record<string, string> = {
  "below value": "The price is at or under the model's value: it asks nothing the business has not already done",
  "within history":
    "The margin or the growth the price needs, either one alone, is no more than the company's best of the last five fiscal years",
  "beyond history":
    "Neither the margin nor the growth alone reaches the price without beating the company's own best of the last five fiscal years: the price assumes a better business than the record shows",
};

function needsGrowth(r: ValuationRow): string {
  if (r.impliedGrowth === null) return DASH;
  if (r.impliedGrowthEdge === "above") return `over ${signedPct(r.impliedGrowth)}`;
  if (r.impliedGrowthEdge === "below") return `under ${signedPct(r.impliedGrowth)}`;
  return signedPct(r.impliedGrowth);
}

export type ValuationView = "valued" | "pmcc" | "all";

export function applyView(rows: ValuationRow[], view: ValuationView, q: string): ValuationRow[] {
  return rows.filter((r) => {
    if (q !== "" && !r.symbol.includes(q)) return false;
    if (view === "valued") return r.state === "valued";
    if (view === "pmcc") return r.pmcc;
    return true;
  });
}

export function ValuationTab() {
  const [params, setParams] = useSearchParams();
  const day = params.get("day");
  const { data, isLoading, isError } = useValuation(day);
  const sort = useSort();
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const viewParam = params.get("view");
  const view: ValuationView = viewParam === "all" || viewParam === "pmcc" ? viewParam : "valued";
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const all = data?.rows ?? [];
  const shown = applyView(all, view, q);
  const sorted = sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage([data?.day, view, q, sort.key, sort.dir], 100);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const latest = data?.days[0] ?? null;
  const valued = all.filter((r) => r.state === "valued").length;
  const states = Object.entries(data?.states ?? {}).filter(([k]) => k !== "valued");

  return (
    <section className="card">
      <div className="card-head">
        <h2>Fair value</h2>
        <span className="chip">ours</span>
        {data?.day && <span className="card-asof">session {data.day}</span>}
      </div>

      <p className="muted" style={{ margin: "0 0 10px" }}>
        <strong>Tested 2018–26: no edge.</strong> A discounted-cash-flow estimate of each share's value from the company's own
        statements: five forecast years from the last five fiscal years' medians (growth from the consensus next-year
        sales, faded to {pct(data?.rule.terminalGrowth ?? 0.02, 0)}), discounted at a cost of capital from a regression
        beta, with a perpetuity after. A model's number, not a price: the range is what a point either way in the rate
        and half a point in the growth do to it. Tested on month-ends 2018–26, the names that
        read cheapest did no better over the next three months than those that read richest, so the rows are in symbol
        order and the figure sits beside the income lists as a description, never a reason to pick a name. The
        last three columns turn it around: the margin, or the growth, the price needs with everything else
        held, beside the company's own best of five years. "Beyond history" means the price assumes a better
        business than the record shows: for a long-dated call, that is how much has to go right, not a forecast.
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="Session"
            value={data.day ?? ""}
            onChange={(e) => set("day", e.target.value === latest ? null : e.target.value)}
          >
            {data.days.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}
        <div className="mode-toggle" style={{ marginLeft: 0 }}>
          {(["valued", "pmcc", "all"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={view === v ? "mode-btn active" : "mode-btn"}
              title={
                v === "valued"
                  ? "Only the names the model valued"
                  : v === "pmcc"
                    ? "pmcc's configured symbols, valued or not (its funds read not applicable)"
                    : "Every name on the list, with why the model has no value for it"
              }
              onClick={() => set("view", v === "valued" ? null : v)}
            >
              {v === "valued" ? "Valued" : v === "pmcc" ? "pmcc" : "All"}
            </button>
          ))}
        </div>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip" style={{ whiteSpace: "normal" }}>
          {shown.length} of {all.length} names · {valued} valued
          {states.length > 0 ? ` · ${states.map(([k, n]) => `${n} ${k}`).join(", ")}` : ""}
        </span>
      </div>

      {isError && <p className="muted">Could not read the fair-value file.</p>}
      {isLoading && <p className="muted">Reading the fair-value file…</p>}
      {data && data.day === null && (
        <p className="muted">
          No fair-value file built yet: it is built at 06:28 ET from the history store, after the statements landing
          (technicals.history).
        </p>
      )}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh label="Symbol" k="symbol" sort={sort} />
                <th title="The model's verdict on the name; a non-valued state says why">State</th>
                <SortTh label="Price" k="price" sort={sort} className="num" title="The last close at or before the session" />
                <SortTh label="Fair" k="fair" sort={sort} className="num" title="The base case per share" />
                <th className="num" title="The grid's minimum and maximum: WACC a point either way, terminal growth half a point either way">
                  Range
                </th>
                <SortTh label="Vs fair" k="gap" sort={sort} className="num" title="Price ÷ fair value − 1: positive when the price sits above the model's value" />
                <SortTh label="WACC" k="wacc" sort={sort} className="num" title="Cost of equity (the 10-year Treasury + beta × 5%) and after-tax cost of debt, weighted by market cap and debt, floored at 7%" />
                <SortTh label="β" k="beta" sort={sort} className="num" title="OLS of weekly log returns on SPY over three years, bounded 0.5-2.0; the broker's beside it when it differs" />
                <SortTh label="Growth" k="growth" sort={sort} className="num" title="Year-one revenue growth: the consensus next-year sales when the store has it, else the five-year CAGR, bounded" />
                <SortTh label="Margin" k="margin" sort={sort} className="num" title="The median operating margin of the last three fiscal years" />
                <SortTh label="TV share" k="terminal" sort={sort} className="num" title="The discounted terminal value's share of enterprise value; over 85% the answer is the perpetuity" />
                <SortTh label="EV/EBITDA" k="multiple" sort={sort} className="num" title="The model's enterprise value over the last fiscal year's EBITDA, a sanity figure" />
                <SortTh label="Needs margin" k="needsMargin" sort={sort} className="num" title="Reverse DCF: the long-run operating margin at which the model's value equals the price, everything else held; the company's best of the last five fiscal years beside it. A dash: no margin under 100% of revenue gets there" />
                <SortTh label="Needs growth" k="needsGrowth" sort={sort} className="num" title="Reverse DCF: the year-one revenue growth, faded to 2% by year five as the base case is, at which the value equals the price, everything else held; the company's best year of the last five beside it" />
                <SortTh label="Reach" k="reach" sort={sort} title="Whether one lever alone reaches the price within the company's own record: below value, within history, beyond history" />
                <th title="The builder's flags">Flags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td>
                    <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
                      {r.symbol}
                    </Link>
                    {r.type && r.type !== "Stock" ? <span className="muted"> · {r.type === "Exchange Traded Fund" ? "fund" : r.type}</span> : null}
                    {r.pmcc ? (
                      <span className="chip" title="A symbol pmcc trades (its config)">
                        pmcc
                      </span>
                    ) : null}
                  </td>
                  <td className={r.state === "valued" ? undefined : "muted"} title={r.reason ?? undefined}>
                    {STATE_LABEL[r.state] ?? r.state}
                    {r.state !== "valued" && r.reason ? <span className="muted"> · {r.reason}</span> : null}
                  </td>
                  <td className="num">{money(r.spot)}</td>
                  <td className="num">{money(r.fairValue)}</td>
                  <td className="num">{r.low === null || r.high === null ? DASH : `${money(r.low)} – ${money(r.high)}`}</td>
                  <td className="num">{signedPct(r.gap)}</td>
                  <td className="num">{pct(r.wacc)}</td>
                  <td className="num" title={r.betaRaw !== null ? `raw ${r.betaRaw.toFixed(2)}${r.betaBroker !== null ? `, broker ${r.betaBroker.toFixed(2)}` : ""}` : undefined}>
                    {r.beta === null ? DASH : r.beta.toFixed(2)}
                    {r.betaBroker !== null && r.flags.includes("beta_disagrees") ? <span className="muted"> · {r.betaBroker.toFixed(2)}</span> : null}
                  </td>
                  <td className="num" title={r.consensusUsed ? "From the consensus next-year sales" : "From the five-year CAGR (no consensus in the store)"}>
                    {signedPct(r.growthY1)}
                    {r.state === "valued" && !r.consensusUsed ? <span className="muted"> h</span> : null}
                  </td>
                  <td className="num">{pct(r.ebitMargin)}</td>
                  <td className="num">{pct(r.terminalShare, 0)}</td>
                  <td className="num" title={r.marketCap !== null ? `market cap ${billions(r.marketCap)}` : undefined}>
                    {r.impliedEvEbitda === null ? DASH : `${r.impliedEvEbitda.toFixed(1)}×`}
                  </td>
                  <td className="num" title={r.bestMargin !== null ? `best of the last five years ${pct(r.bestMargin)}` : undefined}>
                    {r.state !== "valued" ? DASH : pct(r.impliedMargin)}
                    {r.bestMargin !== null ? <span className="muted"> · best {pct(r.bestMargin)}</span> : null}
                  </td>
                  <td className="num" title={r.bestGrowth !== null ? `best of the last five years ${signedPct(r.bestGrowth)}` : undefined}>
                    {needsGrowth(r)}
                    {r.bestGrowth !== null ? <span className="muted"> · best {signedPct(r.bestGrowth)}</span> : null}
                  </td>
                  <td className={r.reach === "beyond history" ? undefined : "muted"} title={r.reach ? REACH_TITLE[r.reach] : undefined}>
                    {r.reach ?? DASH}
                  </td>
                  <td>
                    {r.flags.map((f) => (
                      <span key={f} className="chip muted" title={FLAG_TITLE[f] ?? f}>
                        {f.replace(/_/g, " ")}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shown.length > PAGE_SIZES[0] && (
        <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
      )}
      {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}

      <p className="muted" style={{ marginTop: 8 }}>
        The names are the security list's rank 1-2 optionable names{data?.universe ? ` (${data.universe})` : ""}. Statements
        are Dolt's annual rows, counted 45 days after the fiscal year ends; the broker's beta and market cap ride along as
        a cross-check and decide nothing. A name the model cannot value says why instead of showing a number.
      </p>
    </section>
  );
}
