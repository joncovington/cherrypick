import { Fragment, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { CreditSpreadPrice, CreditSpreadRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useCreditSpreadList } from "../../lib/api";
import { EarningsCell } from "./OwnListTab";

/**
 * Our own credit-spread list (2026-10-24): the chart setups' fresh entries as spreads on the 30-45 day
 * monthly, from `packages/technicals` `income-lists` (version 2, `creditspreads.py`). A long setup is a
 * put spread, its short mirror a call spread; the short nearest 0.40 delta, the pick the widest long
 * giving 35-40% of the width at the working price. Every figure is the builder's; this formats and
 * filters, and ranks nothing -- rows are by symbol.
 *
 * NO TESTED EDGE: item 17 of docs/edge-hypotheses-plan.md (run 2026-10-25) found no setup, side, short
 * or width that passed after costs. The tab reads the rows by a SELECTION (the user's choice the same
 * day): the 35-40% credit rule (every row, the default) or one of the study's least-bad mean-reversion
 * put cells (0.20 or 0.40 delta, 5 strikes), which keep only their rows and show their own short and
 * spread (`rowsFor`). Each row still opens its width table.
 */

const PAGE_SIZES = [50, 100, 200, 500] as const;

const SETUP_LABEL: Record<string, string> = {
  trend: "trend",
  pullback: "pullback",
  reversion: "mean reversion",
  breakout: "breakout",
  "trend-short": "trend (short)",
  "pullback-short": "pullback (short)",
  "reversion-short": "mean reversion (short)",
  "breakout-short": "breakdown",
};

function money(v: number | null): string {
  return v === null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function shortDate(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Per share at the working price, signed cash flow: a credit `cr`, a price under zero a debit `db`. */
function credit(v: number): string {
  return `${money(Math.abs(v))} ${v < 0 ? "db" : "cr"}`;
}

/** Whole contract: a per-share figure × 100. */
function contract(v: number | null): string {
  return v === null ? "—" : `$${(v * 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function Flag({ on, onChange, children, title }: { on: boolean; onChange: (on: boolean) => void; children: ReactNode; title: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      <button type="button" title={title} className={on ? "mode-btn active" : "mode-btn"} onClick={() => onChange(!on)}>
        {children}
      </button>
    </div>
  );
}

function ExDividendCell({ r }: { r: CreditSpreadRow }) {
  if (r.spread === "put") return <td className="muted">—</td>;
  const when = r.exDividendDate ? shortDate(r.exDividendDate) : "";
  if (r.exDividend === "in-trade") {
    return (
      <td title="An ex-dividend date before expiry: a short call in the money then can be assigned early for the dividend">
        {when ? `in trade · ${when}` : "in trade"}
      </td>
    );
  }
  if (r.exDividend === "after") return <td className="muted">{`after · ${when}`}</td>;
  if (r.exDividend === "past") {
    return (
      <td className="muted" title="The broker's date is the last one; the next is not posted, so the spread is never called clear">
        {`past · ${when}`}
      </td>
    );
  }
  return (
    <td className="muted" title="No ex-dividend date is known, so the spread is never called clear">
      unknown
    </td>
  );
}

function LiquidCell({ p }: { p: CreditSpreadPrice | null }) {
  if (p === null || p.passes === null) return <td className="muted">—</td>;
  return (
    <td className={p.passes ? undefined : "muted"} title="The leg bars on the spread as one order: every leg two-sided with its open interest, and its cost to trade within the bars">
      {p.passes ? "liquid" : "thin"}
    </td>
  );
}

function SetupsCell({ r }: { r: CreditSpreadRow }) {
  const title = r.signals.map((s) => `${SETUP_LABEL[s.setup] ?? s.setup}: entered on the ${s.date} close`).join("\n");
  return (
    <td title={title}>
      {r.signals.map((s) => `${SETUP_LABEL[s.setup] ?? s.setup} · ${s.ago === 0 ? "last close" : `${s.ago}d ago`}`).join(", ")}
    </td>
  );
}

/** The width table: every declared width, both units, as the builder priced it. */
export function WidthTable({ r, colSpan }: { r: CreditSpreadRow; colSpan: number }) {
  const right = r.spread === "put" ? "P" : "C";
  return (
    <tr className="row-detail">
      <td colSpan={colSpan}>
        <table className="data-table" aria-label={`${r.symbol} ${r.spread} spread widths`}>
          <thead>
            <tr>
              <th>Width</th>
              <th className="num">Long</th>
              <th className="num">Δ</th>
              <th className="num">Width $</th>
              <th className="num" title="Per share at the working price">Credit</th>
              <th className="num" title="Per share at the mid">Mid</th>
              <th className="num" title="Credit ÷ width">Credit %</th>
              <th className="num" title="Whole contract: (width − credit) × 100">Max loss</th>
              <th className="num" title="Credit ÷ max loss">Cr / ML</th>
              <th>Liquid</th>
            </tr>
          </thead>
          <tbody>
            {r.widths.map((w) => {
              const p = w.price;
              const label = w.unit === "strikes" ? `${w.size} strike${w.size === 1 ? "" : "s"}` : `${w.size} × move`;
              const picked = p !== null && r.pick !== null && p.long === r.pick.long;
              return (
                <tr key={`${w.unit}-${w.size}`}>
                  <td title={w.unit === "move" && r.move !== null ? `The listed long nearest ${w.size} × the 1-SD move (${money(r.move)}) beyond the short` : undefined}>
                    {label}
                    {picked ? (
                      <>
                        {" "}
                        <span className="chip" title="The same long as the 35-40% pick">
                          pick
                        </span>
                      </>
                    ) : null}
                  </td>
                  {p === null ? (
                    <td colSpan={9} className="muted">
                      the chain does not reach this width
                    </td>
                  ) : (
                    <>
                      <td className="num">
                        {p.long}
                        {right}
                      </td>
                      <td className="num">{p.longDelta.toFixed(2)}</td>
                      <td className="num">{money(p.width)}</td>
                      <td className="num">{credit(p.credit)}</td>
                      <td className="num">{credit(p.creditMid)}</td>
                      <td className="num">{pct(p.creditWidth)}</td>
                      <td className="num">{contract(p.maxLoss)}</td>
                      <td className="num">{pct(p.creditMaxLoss)}</td>
                      <LiquidCell p={p} />
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </td>
    </tr>
  );
}

export interface CreditSpreadFilters {
  side: "both" | "put" | "call";
  allEarnings: boolean;
  allTrades: boolean;
  pickedOnly: boolean;
  q: string;
}

/**
 * The rows shown and why each hidden row is hidden, layered in the order the filters sit -- side,
 * earnings, liquid, picked, then the symbol search -- so a row failing several is counted once, under
 * the first. A row with no pick has no spread to judge for liquidity, so only "with a pick" hides it.
 */
export function applyFilters(all: CreditSpreadRow[], f: CreditSpreadFilters) {
  const hidden = { side: 0, inTrade: 0, thin: 0, unpicked: 0, unmatched: 0 };
  const shown: CreditSpreadRow[] = [];
  for (const r of all) {
    if (f.side !== "both" && r.spread !== f.side) hidden.side += 1;
    else if (!f.allEarnings && r.earnings === "in-trade") hidden.inTrade += 1;
    else if (!f.allTrades && r.pick !== null && r.pick.passes === false) hidden.thin += 1;
    else if (f.pickedOnly && r.pick === null) hidden.unpicked += 1;
    else if (f.q !== "" && !r.symbol.includes(f.q)) hidden.unmatched += 1;
    else shown.push(r);
  }
  return { shown, hidden };
}

const SORTS: Record<string, (r: CreditSpreadRow) => number | string | null> = {
  symbol: (r) => r.symbol,
  spread: (r) => r.spread,
  price: (r) => r.spot,
  short: (r) => r.short.strike,
  width: (r) => r.pick?.width ?? null,
  credit: (r) => r.pick?.credit ?? null,
  creditPct: (r) => r.pick?.creditWidth ?? null,
  maxLoss: (r) => r.pick?.maxLoss ?? null,
  earnings: (r) => r.earningsDate,
};

const COLUMNS = 15;

/** The default selection: the user's original rule, which reads every row. */
export const DEFAULT_SELECTION = "credit-share";

/** The rows as one selection reads them: the credit rule keeps every row as built; any other keeps
 *  only the rows that selection lists, each carrying that selection's short and spread in place of the
 *  row's, so every column, filter and sort reads the selected rule. */
export function rowsFor(rows: CreditSpreadRow[], selection: string): CreditSpreadRow[] {
  if (selection === DEFAULT_SELECTION) return rows;
  return rows.flatMap((r) => {
    const s = r.selections?.[selection];
    return s ? [{ ...r, short: s.short, pick: s.price }] : [];
  });
}

export function CreditSpreadTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useCreditSpreadList(params.get("day"));
  const sort = useSort();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const sideParam = params.get("side");
  const side: CreditSpreadFilters["side"] = sideParam === "put" || sideParam === "call" ? sideParam : "both";
  const allEarnings = params.get("earnings") === "all";
  const allTrades = params.get("trade") === "all";
  const pickedOnly = params.get("picked") === "only";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const selections = data?.rule.selections ?? [];
  const selection = selections.some((s) => s.id === params.get("selection")) ? (params.get("selection") as string) : DEFAULT_SELECTION;
  const current = selections.find((s) => s.id === selection);
  const all = rowsFor(data?.rows ?? [], selection);
  const { shown, hidden } = applyFilters(all, { side, allEarnings, allTrades, pickedOnly, q });
  const sorted = sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage([data?.day, selection, side, allEarnings, allTrades, pickedOnly, q, sort.key, sort.dir], 100);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const latest = data?.days[0] ?? null;
  const dropped = Object.entries(data?.dropped ?? {});
  const share = data?.rule.creditShare ?? [0.35, 0.4];
  const shareLabel = `${Math.round(share[0] * 100)}–${Math.round(share[1] * 100)}%`;
  const unpickedAll = all.filter((r) => r.pick === null).length;
  const key = (r: CreditSpreadRow) => `${r.symbol}-${r.spread}`;
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  return (
    <section className="card">
      <div className="card-head">
        <h2>Credit spreads</h2>
        <span className="chip">ours</span>
        <span className="chip chip-warn" title={data?.rule.tested ?? "item 17 of docs/edge-hypotheses-plan.md"}>
          no tested edge
        </span>
        {data?.day && (
          <span className="card-asof">
            session {data.day}
            {data.chainTakenAt ? ` · quoted ${new Date(data.chainTakenAt).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" })} ET` : ""}
          </span>
        )}
      </div>

      <p className="muted" style={{ margin: "0 0 10px" }}>
        <strong>No tested edge.</strong> Item 17 of the edge-hypotheses plan tested every setup, side, short and width on
        2020-2025 and none passed after costs. The selections below are rules to read the rows by, not recommendations: the
        35-40% credit rule you trade, and the two mean-reversion put cells the study found least bad.
        {current && current.id !== DEFAULT_SELECTION ? ` Showing: ${current.rule}.` : ""}
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {selections.length > 0 && (
          <div className="mode-toggle" role="group" aria-label="Selection" style={{ marginLeft: 0 }}>
            {selections.map((sel) => (
              <button
                key={sel.id}
                type="button"
                title={sel.rule}
                className={sel.id === selection ? "mode-btn active" : "mode-btn"}
                onClick={() => set("selection", sel.id === DEFAULT_SELECTION ? null : sel.id)}
              >
                {sel.label}
              </button>
            ))}
          </div>
        )}
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
        <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="Side">
          {(["both", "put", "call"] as const).map((s) => (
            <button key={s} type="button" className={side === s ? "mode-btn active" : "mode-btn"} onClick={() => set("side", s === "both" ? null : s)}>
              {s === "both" ? "both" : s === "put" ? "put spreads" : "call spreads"}
            </button>
          ))}
        </div>
        <Flag
          on={!allEarnings}
          onChange={(on) => set("earnings", on ? null : "all")}
          title="Hide names with an announcement between today and this spread's expiry. An unknown date stays shown, marked unknown."
        >
          No earnings in the trade
        </Flag>
        <Flag
          on={!allTrades}
          onChange={(on) => set("trade", on ? null : "all")}
          title="Hide a picked spread that fails the leg bars as one order. A row with no pick stays shown: it has no spread to judge."
        >
          Liquid trade
        </Flag>
        <Flag
          on={pickedOnly}
          onChange={(on) => set("picked", on ? "only" : null)}
          title={`Show only names where some listed width gives ${shareLabel} of the width at the working price. Off by default: most names have none, and their width tables say how near each width comes.`}
        >
          {`With a ${shareLabel} width`}
        </Flag>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip" style={{ whiteSpace: "normal" }}>
          {shown.length} of {all.length} spreads
          {hidden.side > 0 ? ` · ${hidden.side} on the other side` : ""}
          {hidden.inTrade > 0 ? ` · ${hidden.inTrade} with earnings in the trade hidden` : ""}
          {hidden.thin > 0 ? ` · ${hidden.thin} thin hidden` : ""}
          {hidden.unpicked > 0 ? ` · ${hidden.unpicked} with no ${shareLabel} width hidden` : ""}
          {hidden.unmatched > 0 ? ` · ${hidden.unmatched} not matching “${q}”` : ""}
        </span>
      </div>

      {data?.chainComplete === false && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          The afternoon reading did not finish{data.chainProblem ? ` (${data.chainProblem})` : ""}, so some names are missing.
        </p>
      )}
      {isError && <p className="muted">Could not read the list.</p>}
      {isLoading && <p className="muted">Reading the list…</p>}
      {data && data.day === null && (
        <p className="muted">
          No list built yet: the afternoon chains are read at 14:45 ET on a trading day and the list is built from them at 15:10.
        </p>
      )}
      {data && data.day !== null && !data.built && (
        <p className="muted">
          No credit-spread list for this session: it was built before the list existed (2026-10-24), or without the history
          store the setups are read from.
        </p>
      )}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th aria-label="Widths" />
                <SortTh label="Symbol" k="symbol" sort={sort} />
                <SortTh label="Spread" k="spread" sort={sort} title="A long setup is a put spread; its short mirror a call spread" />
                <th title="The setups behind the row, and the close each entered on">Setups</th>
                <SortTh label="Price" k="price" sort={sort} className="num" />
                <th title="The standard monthly 30-45 days out">Expiry</th>
                <SortTh label="Short" k="short" sort={sort} className="num" title="The option nearest 0.40 delta, within 0.05" />
                <th className="num" title={`The pick: the widest listed long giving ${shareLabel} of the width at the working price`}>
                  Long
                </th>
                <SortTh label="Width" k="width" sort={sort} className="num" />
                <SortTh label="Credit" k="credit" sort={sort} className="num" title="Per share at the working price: halfway from the mid to the natural fill" />
                <SortTh label="Credit %" k="creditPct" sort={sort} className="num" title="Credit ÷ width" />
                <SortTh label="Max loss" k="maxLoss" sort={sort} className="num" title="Whole contract: (width − credit) × 100" />
                <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement, against this spread's expiry" />
                <th title="Call spreads: the broker's ex-dividend date against expiry">Ex-div</th>
                <th title="The leg bars on the picked spread as one order">Liquid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const k = key(r);
                const isOpen = open.has(k);
                const right = r.spread === "put" ? "P" : "C";
                return (
                  <Fragment key={k}>
                    <tr>
                      <td>
                        <button
                          type="button"
                          className="mode-btn"
                          aria-expanded={isOpen}
                          aria-label={`${isOpen ? "Hide" : "Show"} ${r.symbol}'s widths`}
                          onClick={() => toggle(k)}
                        >
                          {isOpen ? "▾" : "▸"}
                        </button>
                      </td>
                      <td>
                        <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
                          {r.symbol}
                        </Link>
                        {r.type && r.type !== "Stock" ? <span className="muted"> · {r.type === "Exchange Traded Fund" ? "fund" : r.type}</span> : null}
                        {r.leveraged ? (
                          <span className="chip" title="A daily-reset fund: it decays and gaps in ways its delta does not price">
                            {r.leveraged}
                          </span>
                        ) : null}
                        {r.conflict ? (
                          <span className="chip chip-warn" title="The name signals both sides at once: a put spread and a call spread">
                            both sides
                          </span>
                        ) : null}
                      </td>
                      <td className={r.spread === "put" ? "pnl-pos" : "pnl-neg"}>{r.spread === "put" ? "bull put" : "bear call"}</td>
                      <SetupsCell r={r} />
                      <td className="num">{money(r.spot)}</td>
                      <td>
                        {shortDate(r.expiry)} <span className="muted">{r.dte}d</span>
                        {r.window === "45-60" ? (
                          <span
                            className="chip chip-warn"
                            title="No standard monthly fell 30-45 days out, so this is the 45-60 day monthly: outside the window item 17 studied"
                          >
                            45-60
                          </span>
                        ) : null}
                      </td>
                      <td className="num" title={`Δ ${r.short.delta.toFixed(2)}, IV ${pct(r.short.iv, 0)}, ${money(r.short.bid)} × ${money(r.short.ask)}`}>
                        {r.short.strike}
                        {right} <span className="muted">{Math.abs(r.short.delta).toFixed(2)}</span>
                      </td>
                      {r.pick === null ? (
                        <td colSpan={5} className="muted" title="No listed width gives that share of the width at the working price; open the row for each width's credit">
                          no {shareLabel} width
                        </td>
                      ) : (
                        <>
                          <td className="num">
                            {r.pick.long}
                            {right}
                          </td>
                          <td className="num">{money(r.pick.width)}</td>
                          <td className="num">{credit(r.pick.credit)}</td>
                          <td className="num">{pct(r.pick.creditWidth)}</td>
                          <td className="num">{contract(r.pick.maxLoss)}</td>
                        </>
                      )}
                      <EarningsCell r={r} />
                      <ExDividendCell r={r} />
                      <LiquidCell p={r.pick} />
                    </tr>
                    {isOpen && <WidthTable r={r} colSpan={COLUMNS} />}
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
      {data && data.built && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}
      {data && data.built && all.length === 0 && <p className="muted">No setup entered on a recent close for any name this session.</p>}

      <p className="muted" style={{ marginTop: 8 }}>
        The names are our options-tradable universe. A row is a chart setup that entered on one of the last{" "}
        {data?.rule.recentSessions ?? 5} closes and is still open: trend, pullback, mean reversion or breakout as a put spread,
        and each one's short mirror as a call spread. The spread is on the standard monthly 30–45 days out, the short nearest
        0.40 delta and the long the widest giving {shareLabel} of the width at the working price
        {unpickedAll > 0 ? ` (${unpickedAll} of ${all.length} have no such width)` : ""}. Open a row for its widths: 1, 2, 3 and 5
        strikes, and a quarter, a half and three quarters of the expected move. Credits are per share; max loss is per
        contract.
        {dropped.length > 0 ? ` No row for: ${dropped.map(([k, v]) => `${v} (${k})`).join(", ")}.` : ""}
      </p>
    </section>
  );
}
