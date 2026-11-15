import { useSearchParams } from "react-router-dom";
import type {
  SignalDistanceRead,
  SignalHorizonRead,
  SignalSideRead,
  SignalStudyList,
  SignalStudyResult,
} from "@console/shared";
import { Card } from "../../components/DataTable";
import { signTone, useBridged } from "../../lib/bridged";

/**
 * Compare → signals (#1179): a signal studied on the underlying alone, before any option is priced.
 * Each result is `packages/backtest`'s (`signalstudy.py`, read through `/api/signals`); nothing here
 * recomputes a figure or labels one better or worse. Tone is a number's sign.
 *
 * The study in view rides in the URL (`?study=<name>`), so a link opens the same one.
 *
 * Results over technicals' daily history and the recorder's minutes are always readable. One over the
 * vendor's store is licensed: the server lists it only while the backtest feature is on, and says how
 * many it held back.
 */

/** A percent with its sign, `+0.12%`; a null is an em dash. */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(digits)}%`;
}

/** A share 0..1 as a whole percent, `56%`; a null is an em dash. */
export function fmtShare(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v * 100)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

/** What a horizon reads: `0` is the firing session's close, N is N sessions on. */
export function horizonLabel(h: number): string {
  if (h === 0) return "to the close";
  return h === 1 ? "next close" : `${h} sessions on`;
}

/** Minutes since midnight Eastern as `10:35`; a daily bar's close is the close. */
export function minuteLabel(m: number): string {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function distanceLabel(d: { unit: string; value: number }): string {
  if (d.unit === "pct") return `${d.value}%`;
  if (d.unit === "em") return `${d.value} × expected move`;
  return `${d.value} points`;
}

const SOURCE_WORDS: Record<string, string> = {
  history: "technicals' history (public)",
  recorder: "the gex recorder's minutes",
  vendor: "the vendor's minute store (licensed)",
};

/** One sentence per horizon, in words: the firings' average move and how often they closed up, beside
 *  the control's. No judgement: the reader weighs the difference. */
export function horizonSentence(h: SignalHorizonRead): string {
  const f = h.fired;
  const c = h.control;
  if (!f.n) return `${horizonLabel(h.horizon)}: no firing had a full window.`;
  const head = `${horizonLabel(h.horizon)}: after ${f.n} firing${f.n === 1 ? "" : "s"} the price moved ${fmtPct(f.mean_pct)} on average and closed higher ${fmtShare(f.up_share)} of the time`;
  if (!c.n) return `${head}; there was no control to compare.`;
  const t = h.difference?.t;
  return `${head}, against ${fmtPct(c.mean_pct)} and ${fmtShare(c.up_share)} on ${c.n} control windows (difference ${fmtPct(h.difference?.mean_pct)}${
    t === null || t === undefined ? "" : `, t ${t.toFixed(1)}`
  }).`;
}

/** The sessions the study left out, in words, naming only the reasons that left any out. */
export function droppedSentence(d: SignalStudyResult["dropped_sessions"]): string {
  const parts = [
    d.not_trading_day > 0 ? `${d.not_trading_day} session${d.not_trading_day === 1 ? "" : "s"} dated on a day the exchange was shut` : "",
    d.incomplete > 0 ? `${d.incomplete} session${d.incomplete === 1 ? "" : "s"} whose bars stop before the close` : "",
  ].filter((p) => p !== "");
  return parts.length === 0 ? "" : ` Left out: ${parts.join(" and ")}.`;
}

function SideRow({ label, s, em }: { label: string; s: SignalSideRead; em: boolean }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{s.n}</td>
      <td className={`num ${signTone(s.mean_pct)}`}>{fmtPct(s.mean_pct)}</td>
      <td className={`num ${signTone(s.median_pct)}`}>{fmtPct(s.median_pct)}</td>
      <td className="num">{fmtPct(s.sd_pct).replace(/^\+/, "")}</td>
      <td className="num">{fmtShare(s.up_share)}</td>
      <td className="num">{fmtPct(s.mean_up_pct)}</td>
      <td className="num">{fmtPct(s.mean_down_pct === undefined || s.mean_down_pct === null ? s.mean_down_pct : -s.mean_down_pct)}</td>
      {em && <td className="num">{fmtNum(s.mean_range_em)}</td>}
      {em && <td className="num">{fmtShare(s.beyond_em_share)}</td>}
    </tr>
  );
}

function Horizons({ doc }: { doc: SignalStudyResult }) {
  const em = doc.em_index !== null;
  return (
    <Card title="After each firing, against the control">
      <div className="table-scroll">
        <table className="data-table num-from-2">
          <thead>
            <tr>
              <th>Horizon</th>
              <th className="num">n</th>
              <th className="num">Mean move</th>
              <th className="num">Median</th>
              <th className="num">Spread (sd)</th>
              <th className="num">Closed up</th>
              <th className="num" title="The average of the highest high after the entry, above the entry price">
                Mean high
              </th>
              <th className="num" title="The average of the lowest low after the entry, below the entry price">
                Mean low
              </th>
              {em && (
                <th className="num" title={`The larger of the high and low excursions over the expected move priced from ${doc.em_index ?? ""}`}>
                  Range ÷ EM
                </th>
              )}
              {em && <th className="num">Beyond 1 EM</th>}
            </tr>
          </thead>
          <tbody>
            {doc.horizons.map((h) => (
              <HorizonRows key={h.horizon} h={h} em={em} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        The difference's t is descriptive: windows overlap when a horizon is longer than the gap between firings, and the t takes
        no account of it.
      </p>
    </Card>
  );
}

function HorizonRows({ h, em }: { h: SignalHorizonRead; em: boolean }) {
  return (
    <>
      <SideRow label={`${horizonLabel(h.horizon)} · fired`} s={h.fired} em={em} />
      <SideRow label="control" s={h.control} em={em} />
      <tr className="muted">
        <td>difference</td>
        <td className="num" />
        <td className={`num ${signTone(h.difference?.mean_pct)}`}>{fmtPct(h.difference?.mean_pct)}</td>
        <td className="num" colSpan={em ? 7 : 5}>
          {h.difference?.t === null || h.difference?.t === undefined ? "" : `t ${h.difference.t.toFixed(2)}`}
          {h.incomplete > 0 ? ` · ${h.incomplete} firing${h.incomplete === 1 ? "" : "s"} without a full window` : ""}
        </td>
      </tr>
    </>
  );
}

function distanceCell(d: SignalDistanceRead | undefined, side: "either" | "up" | "down"): string {
  return d === undefined || !d.n ? "—" : fmtShare(d[side]);
}

function Distances({ doc }: { doc: SignalStudyResult }) {
  const asked = doc.request.distances;
  if (asked.length === 0) return null;
  return (
    <Card title="How often a distance was reached">
      <div className="table-scroll">
        <table className="data-table num-from-2">
          <thead>
            <tr>
              <th>Horizon</th>
              <th>Distance</th>
              <th className="num">Fired: either</th>
              <th className="num">above</th>
              <th className="num">below</th>
              <th className="num">Control: either</th>
              <th className="num">above</th>
              <th className="num">below</th>
            </tr>
          </thead>
          <tbody>
            {doc.horizons.flatMap((h) =>
              asked.map((d, i) => {
                const f = h.fired.distances?.[i];
                const c = h.control.distances?.[i];
                return (
                  <tr key={`${h.horizon}-${i}`}>
                    <td>{i === 0 ? horizonLabel(h.horizon) : ""}</td>
                    <td>{distanceLabel(d)}</td>
                    <td className="num">{distanceCell(f, "either")}</td>
                    <td className="num">{distanceCell(f, "up")}</td>
                    <td className="num">{distanceCell(f, "down")}</td>
                    <td className="num">{distanceCell(c, "either")}</td>
                    <td className="num">{distanceCell(c, "up")}</td>
                    <td className="num">{distanceCell(c, "down")}</td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
      <p className="muted">Reached at any bar after the entry, through the horizon's close, measured from the firing's price.</p>
    </Card>
  );
}

function ByYear({ doc }: { doc: SignalStudyResult }) {
  const hs = doc.horizons.map((h) => h.horizon);
  return (
    <Card title="By year">
      <div className="table-scroll">
        <table className="data-table num-from-2">
          <thead>
            <tr>
              <th>Year</th>
              {hs.map((h) => (
                <th key={h} className="num" colSpan={3}>
                  {horizonLabel(h)}: fired (n) · control · difference
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {doc.by_year.map((y) => (
              <tr key={y.year}>
                <td>{y.year}</td>
                {y.horizons.map((h) => (
                  <YearCells key={h.horizon} h={h} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function YearCells({ h }: { h: SignalStudyResult["by_year"][number]["horizons"][number] }) {
  return (
    <>
      <td className={`num ${signTone(h.fired_mean_pct)}`}>
        {fmtPct(h.fired_mean_pct)} ({h.fired_n})
      </td>
      <td className={`num ${signTone(h.control_mean_pct)}`}>{fmtPct(h.control_mean_pct)}</td>
      <td className={`num ${signTone(h.difference?.mean_pct)}`}>{fmtPct(h.difference?.mean_pct)}</td>
    </>
  );
}

function Firings({ doc }: { doc: SignalStudyResult }) {
  const hs = doc.horizons.map((h) => h.horizon);
  const intraday = doc.request.bar_minutes !== undefined;
  return (
    <Card title={`Every firing (${doc.firings.length})`} collapseKey="signals-firings" defaultCollapsed>
      <div className="table-scroll">
        <table className="data-table num-from-2">
          <thead>
            <tr>
              <th>Session</th>
              {intraday && <th>Time</th>}
              <th className="num">Price</th>
              <th className="num">Value</th>
              {hs.map((h) => (
                <th key={h} className="num">
                  {horizonLabel(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...doc.firings].reverse().map((f) => (
              <tr key={`${f.session}-${f.minute}`}>
                <td>{f.session}</td>
                {intraday && <td>{minuteLabel(f.minute)}</td>}
                <td className="num">{f.price.toFixed(2)}</td>
                <td className="num">{fmtNum(f.value)}</td>
                {hs.map((h) => {
                  const o = f[`h${h}`];
                  return (
                    <td key={h} className={`num ${signTone(o?.move_pct)}`}>
                      {o ? fmtPct(o.move_pct) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function StudyBody({ doc }: { doc: SignalStudyResult }) {
  const req = doc.request;
  const droppedWords = droppedSentence(doc.dropped_sessions);
  return (
    <>
      <Card title={`${doc.name} · ${req.symbol}`}>
        <p>
          <strong>{String(req.signal["kind"] ?? "signal").replace(/_/g, " ")}</strong> on {req.symbol}
          {req.bar_minutes !== undefined ? `, ${req.bar_minutes}-minute bars` : ", daily bars"}, from {SOURCE_WORDS[req.source] ?? req.source}
          . {doc.bars.first} to {doc.bars.last}, {doc.bars.sessions.toLocaleString("en-US")} sessions read. It fired{" "}
          {doc.firings_n.toLocaleString("en-US")} time{doc.firings_n === 1 ? "" : "s"} on {doc.firing_sessions.toLocaleString("en-US")}{" "}
          session{doc.firing_sessions === 1 ? "" : "s"}.{droppedWords}
        </p>
        <ul>
          {doc.horizons.map((h) => (
            <li key={h.horizon}>{horizonSentence(h)}</li>
          ))}
        </ul>
        <p className="muted">
          The control is the same minute on sessions that had not fired by then, weighted to the minutes the signal fired at.
          {doc.em_index !== null
            ? ` The expected move is priced from ${doc.em_index}'s last close known at the entry.`
            : ` ${req.symbol} has no volatility index here, so there is no expected move.`}
          {doc.licensed ? " Licensed: computed from the vendor's data, never shared." : ""}
          {doc.source?.reads_holdout ? " Its sessions reach the configured holdout window." : ""}
        </p>
        <details>
          <summary className="muted">The signal as written</summary>
          <pre className="mono">{JSON.stringify(req.signal, null, 2)}</pre>
        </details>
      </Card>
      <Horizons doc={doc} />
      <Distances doc={doc} />
      <ByYear doc={doc} />
      <Firings doc={doc} />
    </>
  );
}

function Study({ name }: { name: string }) {
  const q = useBridged<SignalStudyResult>(["signal-study", name], `/api/signals/${encodeURIComponent(name)}`, 300_000);
  if (q.isLoading) return <p className="muted">Reading {name}…</p>;
  if (q.error || !q.data?.ok || !q.data.data) return <p className="pnl-neg">{q.data?.error ?? `${name} could not be read`}</p>;
  return <StudyBody doc={q.data.data} />;
}

export function SignalsTab() {
  const [params, setParams] = useSearchParams();
  const q = useBridged<SignalStudyList>(["signal-studies"], "/api/signals", 120_000);
  if (q.isLoading) return <p className="muted">Reading the signal studies…</p>;
  if (q.error || !q.data?.ok || !q.data.data) return <p className="pnl-neg">{q.data?.error ?? "signal studies unavailable"}</p>;
  const { studies, unreadable, withheld } = q.data.data;
  const notes = (
    <>
      {withheld > 0 && (
        <p className="muted">
          {withheld} licensed stud{withheld === 1 ? "y is" : "ies are"} not listed: the backtest feature is off.
        </p>
      )}
      {unreadable.length > 0 && <p className="pnl-neg">Could not read: {unreadable.join(", ")}.</p>}
    </>
  );
  if (studies.length === 0) {
    return (
      <>
        <p className="muted">
          No signal studies yet. Write a request and run{" "}
          <code>python packages/backtest/scripts/signal_study.py run --request &lt;file&gt;</code>, or queue it as a research row.
        </p>
        {notes}
      </>
    );
  }
  const asked = params.get("study");
  const current = studies.find((s) => s.name === asked) ?? studies[0]!;
  return (
    <>
      <p className="muted">
        What the underlying did after each firing of a signal, before any option is priced. A description, never a verdict: a signal
        worth trading is declared and tested before anything changes.
      </p>
      <label>
        Study{" "}
        <select
          value={current.name}
          onChange={(e) => {
            const next = new URLSearchParams(params);
            next.set("study", e.target.value);
            setParams(next, { replace: true });
          }}
        >
          {studies.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} · {s.symbol} · {s.kind.replace(/_/g, " ")} · {s.firings} firings{s.licensed ? " · licensed" : ""}
            </option>
          ))}
        </select>
      </label>
      {notes}
      <Study name={current.name} />
    </>
  );
}
