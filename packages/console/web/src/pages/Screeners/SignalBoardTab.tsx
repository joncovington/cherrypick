import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { SignalBoardPayload, SignalBoardRow, SignalTag } from "@console/shared";
import { useSignalBoard } from "../../lib/api";

/**
 * The signal board (2026-10-17): each listed stock's count of methods agreeing on one side, from our
 * own engines -- RS stage, its sector's rotation, the trend scan and that session's analyst revisions
 * -- laid out as the vendor's board is, bullish and bearish lists ordered by count. Read-only, and it
 * decides nothing: `python -m cherrypick.technicals board` writes every figure each morning.
 *
 * Only the side's tags are listed (the board lists no other); a tie has no side and is not listed.
 * Rotation and the scan are marked approximate where the file says so, and the methods we do not
 * count are named under the tables. Grouped lists keep their order, so the tables do not sort.
 */

const MIN_COUNTS = [1, 2, 3] as const;

function Flag({ on, onChange, children, title }: { on: boolean; onChange: (on: boolean) => void; children: ReactNode; title: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      <button type="button" title={title} className={on ? "mode-btn active" : "mode-btn"} onClick={() => onChange(!on)}>
        {children}
      </button>
    </div>
  );
}

function TagChip({ t, data }: { t: SignalTag; data: SignalBoardPayload }) {
  const m = data.methods[t.method];
  const approx = m?.approximate === true;
  const title = `${m?.label ?? t.method}${t.detail ? `: ${t.detail}` : ""}${approx ? ". Approximate: our engine differs from the board it reproduces." : ""}`;
  return (
    <span className="chip" title={title}>
      {approx ? "≈ " : ""}
      {t.label}
    </span>
  );
}

function BoardTable({ rows, data, side }: { rows: SignalBoardRow[]; data: SignalBoardPayload; side: "bullish" | "bearish" }) {
  return (
    <div className="table-scroll" style={{ marginBottom: 12 }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th className="num" title="Methods agreeing on this side">
              Count
            </th>
            <th title="The side's tags only, as the board lists them">Signals</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>
                <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
                  {r.symbol}
                </Link>
              </td>
              <td style={{ whiteSpace: "normal", maxWidth: 160 }}>{r.sector ?? "—"}</td>
              <td
                className={`num ${side === "bullish" ? "pnl-pos" : "pnl-neg"}`}
                title={`${r.bullish} bullish, ${r.bearish} bearish; the board counts the larger side`}
              >
                {r.count}
              </td>
              <td style={{ whiteSpace: "normal" }}>
                <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
                  {r.tags.map((t) => (
                    <TagChip key={t.method} t={t} data={data} />
                  ))}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SignalBoardTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useSignalBoard(params.get("day"));
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const min = Number(params.get("min") ?? "2");
  const sector = params.get("sector");
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const latest = data?.days[0] ?? null;

  const all = data?.rows ?? [];
  const keep = (r: SignalBoardRow) => r.count >= min && (sector === null || r.sector === sector) && (q === "" || r.symbol.includes(q));
  // The file is ordered by count, then symbol; the lists keep that order.
  const bull = all.filter((r) => r.side === "bullish" && keep(r));
  const bear = all.filter((r) => r.side === "bearish" && keep(r));
  const sectors = [...new Set(all.map((r) => r.sector).filter((s): s is string => s !== null))].sort();
  const counted = data ? Object.values(data.methods).map((m) => (m.approximate ? `${m.label} (approximate)` : m.label)) : [];

  return (
    <section className="card">
      <div className="card-head">
        <h2>Signal board</h2>
        <span className="chip">read-only</span>
        {data?.day && (
          <span className="card-asof">
            session {data.day} · {data.names} stocks read
            {data.revisionsThrough ? ` · revisions fetched ${data.revisionsThrough.slice(0, 16).replace("T", " ")} UTC` : " · no revisions fetched"}
            {data.fundamentalsSnapshot ? ` · estimates of ${data.fundamentalsSnapshot}` : ""}
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="Board session"
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
        {MIN_COUNTS.map((n) => (
          <Flag key={n} on={min === n} onChange={() => set("min", n === 2 ? null : String(n))} title={`Names with ${n} or more agreeing methods`}>
            {n}+
          </Flag>
        ))}
        <select className="chip review-session-select" aria-label="Sector" value={sector ?? ""} onChange={(e) => set("sector", e.target.value || null)}>
          <option value="">all sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip">
          {bull.length} bullish · {bear.length} bearish
        </span>
      </div>

      {isError && <p className="muted">Could not read the signal board.</p>}
      {isLoading && <p className="muted">Reading the board…</p>}
      {data && data.day === null && <p className="muted">No board written yet: the board job runs at 06:45 ET.</p>}

      {data && data.day !== null && (
        <>
          <h3>Bullish</h3>
          {bull.length > 0 ? <BoardTable rows={bull} data={data} side="bullish" /> : <p className="muted">None at {min}+.</p>}
          <h3>Bearish</h3>
          {bear.length > 0 ? <BoardTable rows={bear} data={data} side="bearish" /> : <p className="muted">None at {min}+.</p>}
        </>
      )}

      <p className="muted" style={{ marginTop: 8 }}>
        A name's count is its methods agreeing on one side, and only that side's signals are listed, as the board it reproduces
        lists them. Counted: {counted.join(", ") || "—"}. Not counted yet: {data?.notCounted.join(", ") || "—"}, so counts run
        lower than the original board's and no three-signal floor is applied. Scored on the 15 saved boards (2026-09-23 to
        10-14), our stage tag found 69 of 73, analyst revisions 33 of 38, rotation 89 of 96, fundamentals 59 of 95
        (compelling 43 of 55, weak 16 of 40), the scan 9 of 16. Fundamentals vs price reproduced under half its tags, so it
        is not counted. History found no edge in the direction read or in revisions on their own: this is the board's view,
        not a tested signal.
      </p>
    </section>
  );
}
