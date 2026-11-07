import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { SignalBoardPayload, SignalBoardRow, SignalTag } from "@console/shared";
import { useSignalBoard } from "../../lib/api";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, FilterSortButton, FilterSortPanel, FilterSummary, FilterSwitch } from "../../components/FilterSort";
import { QuestionSection, ScreenerLayout } from "./QuestionSection";

/**
 * The signal board (2026-10-17): each listed stock's count of methods agreeing on one side, from our
 * own engines -- RS stage, its sector's rotation, the trend scan and that session's analyst revisions
 * -- laid out as the vendor's board is, bullish and bearish lists ordered by count. Read-only, and it
 * decides nothing: `python -m cherrypick.technicals board` writes every figure each morning.
 *
 * 2026-11-06: bullish and bearish are two cards side by side (one each on a phone), each name a row
 * with a count meter and its signal chips; the method note is behind each card's ?, its last line an
 * amber flag; the minimum count, sector and symbol search sit behind filter & sort.
 *
 * Only the side's tags are listed (the board lists no other); a tie has no side and is not listed.
 * Rotation and the scan are marked approximate where the file says so, and the methods we do not
 * count are named in the help. Lists keep the file's order (count, then symbol).
 */

const MIN_COUNTS = [1, 2, 3] as const;

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

function BoardList({ rows, data, side, min }: { rows: SignalBoardRow[]; data: SignalBoardPayload; side: "bullish" | "bearish"; min: number }) {
  if (rows.length === 0) return <p className="muted">None at {min}+.</p>;
  const top = Math.max(Object.keys(data.methods).length, ...rows.map((r) => r.count));
  return (
    <ul className="sb-list">
      {rows.map((r) => (
        <li key={r.symbol} className="sb-row">
          <span className="sb-name">
            <Link className="module-link mono" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
              {r.symbol}
            </Link>
            <span className="muted sb-sector">{r.sector ?? "—"}</span>
          </span>
          <span
            className={`sb-count ${side === "bullish" ? "pnl-pos" : "pnl-neg"}`}
            title={`${r.bullish} bullish, ${r.bearish} bearish; the board counts the larger side`}
          >
            {r.count}
          </span>
          <span className="sb-meter" aria-hidden="true">
            <span className={`sb-meter-fill ${side === "bullish" ? "is-bull" : "is-bear"}`} style={{ width: `${Math.min(100, (100 * r.count) / top)}%` }} />
          </span>
          <span className="sb-chips">
            {r.tags.map((t) => (
              <TagChip key={t.method} t={t} data={data} />
            ))}
          </span>
        </li>
      ))}
    </ul>
  );
}

export const boardHelp = (data: SignalBoardPayload): CardHelpContent => {
  const counted = Object.values(data.methods).map((m) => (m.approximate ? `${m.label} (approximate)` : m.label));
  return {
    title: "Reading the board",
    body: (
      <>
        <HelpWarn title="Not a tested signal">
          <p>History found no edge in the direction read or in revisions on their own: this is the board's view, not a tested signal.</p>
        </HelpWarn>
        <HelpSection title="A count">
          <p>
            A name's methods agreeing on one side, and only that side's signals are listed, as the board it reproduces lists them. A tie
            has no side and is not listed. ≈ marks an approximate read. Counted: {counted.join(", ") || "—"}. Not counted yet:{" "}
            {data.notCounted.join(", ") || "—"}, so counts run lower than the original board's and no three-signal floor is applied.
          </p>
        </HelpSection>
        <HelpSection title="How well each method reproduces the board">
          <p>
            Scored on the 15 saved boards (2026-09-23 to 10-14), our stage tag found 69 of 73, analyst revisions 33 of 38, rotation 89 of 96,
            fundamentals 74 of 95, fundamentals vs price 29 of 50, the scan 9 of 16; with the stage and scan rules refit on 2026-10-27,
            every saved board re-scored gives the stage 114 of 115 and the scan 17 of 21. Fundamentals compare each company with its
            industry peers (its sector where an industry has fewer than five names), by our own measures rather than an exact copy of the
            original board's.
          </p>
        </HelpSection>
      </>
    ),
  };
};

export function SignalBoardTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useSignalBoard(params.get("day"));
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
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

  const changed = (min !== 2 ? 1 : 0) + (sector !== null ? 1 : 0) + (q !== "" ? 1 : 0);
  const reset = () => {
    const next = new URLSearchParams(params);
    for (const k of ["min", "sector", "q"]) next.delete(k);
    setParams(next, { replace: true });
  };
  const help = data ? boardHelp(data) : undefined;
  const filterButton = <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />;

  return (
    <ScreenerLayout>
      <div className="sb-bar">
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
        {filterButton}
        {data?.day && (
          <span className="card-asof">
            session {data.day} · {data.names} stocks read
            {data.revisionsThrough ? ` · revisions fetched ${data.revisionsThrough.slice(0, 16).replace("T", " ")} UTC` : " · no revisions fetched"}
            {data.fundamentalsSnapshot ? ` · estimates of ${data.fundamentalsSnapshot}` : ""}
          </span>
        )}
      </div>
      <FilterSummary
        parts={[`${min}+ agreeing`, sector ?? "all sectors", ...(q ? [`"${q}"`] : [])]}
        note={`${bull.length} bullish · ${bear.length} bearish`}
        changed={changed}
        onOpen={() => setPanel(true)}
        onReset={reset}
      />

      {isError && <p className="muted">Could not read the signal board.</p>}
      {isLoading && <p className="muted">Reading the board…</p>}
      {data && data.day === null && <p className="muted">No board written yet: the board job runs at 06:45 ET.</p>}

      {data && data.day !== null && (
        <QuestionSection concept="dir" name="agreement" question="Where do our methods agree?">
          <Card title="bullish" help={help} flag="not a tested signal" controls={<span className="chip">{bull.length}</span>}>
            <BoardList rows={bull} data={data} side="bullish" min={min} />
          </Card>
          <Card title="bearish" help={help} flag="not a tested signal" controls={<span className="chip">{bear.length}</span>}>
            <BoardList rows={bear} data={data} side="bearish" min={min} />
          </Card>
        </QuestionSection>
      )}

      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <FilterGroup title="min count" note="methods agreeing on one side">
          {MIN_COUNTS.map((n) => (
            <FilterSwitch
              key={n}
              label={`${n}+`}
              description={`Names with ${n} or more agreeing methods${n === 2 ? " (the default)" : ""}`}
              on={min === n}
              onChange={() => set("min", n === 2 ? null : String(n))}
            />
          ))}
        </FilterGroup>
        <FilterGroup title="sector and symbol">
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
        </FilterGroup>
      </FilterSortPanel>
    </ScreenerLayout>
  );
}
