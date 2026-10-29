import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ValuationRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useValuation } from "../../lib/api";
import { ValuationDetail, flagText, money, pct, stateLabel, stretch, stretchText } from "./ValuationDetail";

export { ValuationDetail, stretch } from "./ValuationDetail";

/**
 * The fair-value tab (2026-10-27): one DCF estimate an options-tradable name a session, from
 * `packages/technicals` `valuation` (`cherrypick.core.valuation` on Dolt's annual statements). Every
 * figure is the builder's; this formats and filters. It never orders by the gap (tested, no edge): by
 * default the rows open by how much the price asks of the company (`riskOrder`: reach, then `stretch`,
 * 2026-10-28, the user), the read for a PMCC long leg and for the stock a wheel or skate put may be
 * assigned; a column sort is the reader's own choice.
 *
 * A row carries only what that choice needs (symbol, price, fair value, reach, stretch; 2026-10-28, the
 * user: the full table confused); everything else opens beneath it (`ValuationDetail`). The standing
 * note says the estimate was tested and has no edge and is never removed while the table shows.
 * pmcc's configured stocks carry a badge (its funds read not applicable: a DCF on operating cash flow
 * does not describe a fund, a bank or a REIT).
 */

const PAGE_SIZES = [50, 100, 200, 500] as const;
const DASH = "—";
const COLUMNS = 6;

const SORTS: Record<string, (r: ValuationRow) => number | string | null> = {
  symbol: (r) => r.symbol,
  price: (r) => r.spot,
  fair: (r) => r.fairValue,
  reach: (r) => r.reach,
  stretch: (r) => stretch(r),
};

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

export function ValuationTab() {
  const [params, setParams] = useSearchParams();
  const day = params.get("day");
  const { data, isLoading, isError } = useValuation(day);
  const sort = useSort();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const viewParam = params.get("view");
  const view: ValuationView = viewParam === "all" || viewParam === "pmcc" ? viewParam : "valued";
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const all = data?.rows ?? [];
  const shown = applyView(all, view, q);
  const sorted = sort.key === null ? riskOrder(shown) : sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage([data?.day, view, q, sort.key, sort.dir], 100);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const latest = data?.days[0] ?? null;
  const valued = all.filter((r) => r.state === "valued").length;

  return (
    <section className="card">
      <div className="card-head">
        <h2>Fair value</h2>
        <span className="chip">ours</span>
        {data?.day && <span className="card-asof">session {data.day}</span>}
      </div>

      <p className="muted" style={{ margin: "0 0 10px" }}>
        <strong>Tested 2018–26: no edge.</strong> A model's estimate of each share's value from the company's own
        statements. Cheap names did no better than rich ones, so it is a description, never a reason to pick a name.
        What it is for: how much has to go right for today's price to hold, for a PMCC long leg or a stock a wheel or
        skate put may be assigned. <strong>Reach</strong> says whether the business has ever done what the price needs:
        below value (nothing new), within history (its best year would do), beyond history (better than it has ever
        done). <strong>Stretch</strong> is the margin the price needs over the company's best: under 1× is inside its
        record, over 1× is not. The list opens below value first, then within history, then beyond, each least
        stretched first. Open a row (▸) for the details.
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
                <th aria-label="Details" />
                <SortTh label="Symbol" k="symbol" sort={sort} />
                <SortTh label="Price" k="price" sort={sort} className="num" title="The last close at or before the session" />
                <SortTh label="Fair value" k="fair" sort={sort} className="num" title="The model's value per share" />
                <SortTh label="Reach" k="reach" sort={sort} title="Whether the business has ever done what the price needs: below value, within history, beyond history" />
                <SortTh label="Stretch" k="stretch" sort={sort} className="num" title="The margin the price needs over the company's best of the last five fiscal years: under 1× is inside its record, over 1× is not" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isOpen = open.has(r.symbol);
                return (
                  <Fragment key={r.symbol}>
                    <tr>
                      <td>
                        <button
                          type="button"
                          className="mode-btn"
                          aria-expanded={isOpen}
                          aria-label={`${isOpen ? "Hide" : "Show"} ${r.symbol}'s details`}
                          onClick={() => toggle(r.symbol)}
                        >
                          {isOpen ? "▾" : "▸"}
                        </button>
                      </td>
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
                        {r.flags.length > 0 ? (
                          <span className="chip muted" title={r.flags.map(flagText).join(" ")}>
                            {r.flags.length === 1 ? "1 note" : `${r.flags.length} notes`}
                          </span>
                        ) : null}
                      </td>
                      <td className="num">{money(r.spot)}</td>
                      <td className="num">{money(r.fairValue)}</td>
                      {r.state === "valued" ? (
                        <td className={r.reach === "beyond history" ? undefined : "muted"} title={r.reach ? REACH_TITLE[r.reach] : undefined}>
                          {r.reach ?? DASH}
                        </td>
                      ) : (
                        <td className="muted" title={r.reason ?? undefined}>
                          {stateLabel(r.state)}
                          {r.reason ? ` · ${r.reason.replace(/_/g, " ")}` : ""}
                        </td>
                      )}
                      <td className="num">{stretchText(stretch(r))}</td>
                    </tr>
                    {isOpen && (
                      <tr className="row-detail">
                        <td colSpan={COLUMNS}>
                          <ValuationDetail r={r} rule={data?.rule} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {shown.length > PAGE_SIZES[0] && (
        <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
      )}
      {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}

      <p className="muted" style={{ marginTop: 8 }}>
        How the value is made: five forecast years from the company's last five fiscal years (growth from the analysts'
        next-year sales where there is one, faded to {pct(data?.rule.terminalGrowth ?? 0.02, 0)}), discounted at a
        cost of capital from the stock's beta, with a perpetuity after. The names are the security list's optionable
        names that the liquidity verdict calls options-tradable{data?.universe ? ` (${data.universe})` : ""}. Statements
        are Dolt's annual rows, counted 45 days after the fiscal year ends; the broker's beta and market cap ride along
        as a cross-check and decide nothing.
      </p>
    </section>
  );
}
