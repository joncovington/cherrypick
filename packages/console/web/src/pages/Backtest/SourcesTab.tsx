import { useState } from "react";
import {
  SOURCE_CADENCES,
  SOURCE_EXPIRIES,
  SOURCE_FIELDS,
  type BridgedResult,
  type SourceCoverage,
  type SourceRow,
  type SourcesSummary,
  type SourceState,
} from "@console/shared";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";

/**
 * Backtest → sources (#1194): every data store the suite holds, ours and public, from the source
 * registry (`cherrypick.core.sources`, read through `/api/sources`): what each holds, its cadence, the
 * expiries a chain carries, and its span from the last inventory. Then a coverage question: which
 * source covers a field for a symbol over dates, or why none does.
 *
 * Always shown: our own data needs no licence. The vendor's stores are listed only while their results
 * may be shown, and the server says how many it held back. Nothing here recomputes a span or a verdict.
 */

const KIND_ORDER = ["bars", "index", "chain", "quotes", "iv", "profile", "rates"];
const KIND_WORDS: Record<string, string> = {
  bars: "Price bars",
  index: "Index closes",
  chain: "Option chains",
  quotes: "Intraday quotes",
  iv: "Implied volatility",
  profile: "Gamma profiles",
  rates: "Rates",
};

const STATE_CHIP: Record<SourceState, string> = {
  read: "chip chip-ok",
  absent: "chip",
  skipped: "chip",
  error: "chip chip-bad",
  "not read": "chip chip-warn",
};

/** `2011-01-03 to 2026-11-13`; an em dash when the inventory holds no span. */
export function spanLabel(r: Pick<SourceRow, "first" | "last">): string {
  if (r.first === null || r.last === null) return "—";
  return r.first === r.last ? r.first : `${r.first} to ${r.last}`;
}

/** Sources grouped by kind, in reading order: prices first, rates last; an unknown kind at the end. */
export function byKind(rows: SourceRow[]): Array<[string, SourceRow[]]> {
  const groups = new Map<string, SourceRow[]>();
  for (const r of rows) groups.set(r.kind, [...(groups.get(r.kind) ?? []), r]);
  const rank = (k: string) => (KIND_ORDER.includes(k) ? KIND_ORDER.indexOf(k) : KIND_ORDER.length);
  return [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
}

function SourceTable({ rows }: { rows: SourceRow[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Holds</th>
            <th>Cadence</th>
            <th>Fields</th>
            <th className="num">Symbols</th>
            <th>Span</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} data-source={r.name}>
              <td>
                <code>{r.name}</code>
                {r.licensed ? (
                  <>
                    {" "}
                    <span className="chip chip-warn" title="the data vendor's, kept until the licence's purge date">
                      licensed
                    </span>
                  </>
                ) : null}
              </td>
              <td>
                {r.holds}
                {r.expiries.length > 0 ? <span className="muted"> · expiries: {r.expiries.join(", ")}</span> : null}
                {r.rolling ? <span className="muted"> · keeps a moving window, so its start moves</span> : null}
                {r.note !== null ? <span className="muted"> · {r.note}</span> : null}
                <div className="muted">written by {r.writer}</div>
              </td>
              <td>{r.cadence}</td>
              <td>{r.fields.join(", ")}</td>
              <td className="num">{r.symbols === null ? "—" : r.symbols.toLocaleString("en-US")}</td>
              <td>{spanLabel(r)}</td>
              <td>
                <span className={STATE_CHIP[r.state]} title={r.reason ?? (r.read_at !== null ? `read ${r.read_at}` : "")}>
                  {r.state}
                </span>
                {r.reason !== null && r.state !== "read" ? <div className="muted">{r.reason}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CoverageForm {
  field: string;
  symbol: string;
  from: string;
  to: string;
  cadence: string;
  expiry: string;
}

/** The coverage route's query for a form; empty optional values are left off. */
export function coverageQuery(f: CoverageForm): string {
  const q = new URLSearchParams({ field: f.field, symbol: f.symbol.trim().toUpperCase(), from: f.from, to: f.to });
  if (f.cadence !== "") q.set("cadence", f.cadence);
  if (f.expiry !== "") q.set("expiry", f.expiry);
  return q.toString();
}

function CoverageAnswer({ doc }: { doc: SourceCoverage }) {
  return (
    <>
      <p>
        {doc.covered ? (
          <>
            <span className="chip chip-ok">covered</span> by <code>{doc.best}</code>
          </>
        ) : (
          <span className="chip chip-warn">not covered</span>
        )}{" "}
        {doc.field} for {doc.symbol}, {doc.start} to {doc.end}
        {doc.cadence !== null ? `, at ${doc.cadence} or finer` : ""}
        {doc.expiry !== null ? `, ${doc.expiry} expiries` : ""}.
      </p>
      {doc.sources.length === 0 ? (
        <p className="muted">No source supplies that field at that cadence and expiry.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Cadence</th>
                <th>Span</th>
                <th className="num">Density</th>
                <th>Why not</th>
              </tr>
            </thead>
            <tbody>
              {doc.sources.map((c) => (
                <tr key={c.source}>
                  <td>
                    <code>{c.source}</code> {c.covers ? <span className="chip chip-ok">covers</span> : null}
                  </td>
                  <td>{c.cadence}</td>
                  <td>{spanLabel({ first: c.first ?? null, last: c.last ?? null })}</td>
                  <td className="num">{c.density === null || c.density === undefined ? "—" : `${Math.round(c.density * 100)}%`}</td>
                  <td>{c.why ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">
        Density is the share of the window&apos;s trading days the source holds. A source covers when it reaches both ends and holds at
        least 95% of them.
      </p>
    </>
  );
}

function CoverageCard() {
  const [form, setForm] = useState<CoverageForm>(() => ({ field: "closes", symbol: "SPY", from: "2019-01-02", to: new Date().toISOString().slice(0, 10), cadence: "", expiry: "" }));
  const [answer, setAnswer] = useState<BridgedResult<SourceCoverage> | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof CoverageForm) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function ask() {
    setBusy(true);
    try {
      const res = await fetch(`/api/sources/coverage?${coverageQuery(form)}`);
      const body = (await res.json()) as BridgedResult<SourceCoverage> | { error: string };
      setAnswer("ok" in body ? body : { ok: false, data: null, error: body.error });
    } catch (err) {
      setAnswer({ ok: false, data: null, error: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Does our data cover it?">
      <form
        className="sources-form"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <label>
          Field{" "}
          <select value={form.field} onChange={set("field")}>
            {SOURCE_FIELDS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Symbol <input value={form.symbol} onChange={set("symbol")} size={8} aria-label="symbol" />
        </label>{" "}
        <label>
          From <input type="date" value={form.from} onChange={set("from")} />
        </label>{" "}
        <label>
          To <input type="date" value={form.to} onChange={set("to")} />
        </label>{" "}
        <label>
          Cadence{" "}
          <select value={form.cadence} onChange={set("cadence")}>
            <option value="">any</option>
            {SOURCE_CADENCES.map((c) => (
              <option key={c} value={c}>
                {c} or finer
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Expiry{" "}
          <select value={form.expiry} onChange={set("expiry")}>
            <option value="">any</option>
            {SOURCE_EXPIRIES.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        </label>{" "}
        <button type="submit" disabled={busy}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
      {answer === null ? (
        <p className="muted">Our own and public sources only; the vendor&apos;s are left out.</p>
      ) : answer.ok && answer.data !== null ? (
        <CoverageAnswer doc={answer.data} />
      ) : (
        <p className="pnl-neg">{answer.error}</p>
      )}
    </Card>
  );
}

export function SourcesBody({ doc }: { doc: SourcesSummary }) {
  const read = doc.sources.filter((s) => s.state === "read").length;
  return (
    <>
      <p className="muted">
        {doc.sources.length} sources, {read} read
        {doc.generated_at !== null ? ` by the inventory of ${doc.generated_at}` : "; no inventory has run yet"}. Each store&apos;s span is
        read per symbol, read-only, each morning (<code>sources-inventory</code>, 09:00 ET).
        {doc.withheld > 0 ? ` ${String(doc.withheld)} of the vendor's sources are not listed: their results may not be shown.` : ""}
      </p>
      {doc.undeclared.length > 0 ? (
        <p className="pnl-neg">
          Data files no source declares: {doc.undeclared.join(", ")}. Declare each in <code>cherrypick.core.sources</code>, or say why it is
          not a source.
        </p>
      ) : null}
      <CoverageCard />
      {byKind(doc.sources).map(([kind, rows]) => (
        <Card key={kind} title={`${KIND_WORDS[kind] ?? kind} (${String(rows.length)})`} collapseKey={`sources-${kind}`}>
          <SourceTable rows={rows} />
        </Card>
      ))}
    </>
  );
}

export function BacktestSourcesTab() {
  const q = useBridged<SourcesSummary>(["sources"], "/api/sources", 300_000);
  if (q.isLoading) return <p className="muted">Reading the source registry…</p>;
  if (q.error || !q.data?.ok || !q.data.data) return <p className="pnl-neg">{q.data?.error ?? "source registry unavailable"}</p>;
  return <SourcesBody doc={q.data.data} />;
}
