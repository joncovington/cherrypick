import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { PROFILE_SYMBOLS, type ProfileSymbol } from "@console/shared";
import { useFuturesTicker, useProfileBars } from "../../lib/api";
import { useCandles } from "../../lib/useCandles";
import { useMeasure } from "../../lib/useMeasure";
import { CardHelpButton, HelpSection, type CardHelpContent } from "../../components/CardHelp";
import type { MarketComposite, MarketProfile } from "../../lib/marketProfile";
import { buildView, partialPeriods, valueRelation, type ProfileView } from "./profileView";
import { SessionDetail } from "./SessionDetail";

/**
 * The Charts chart's sessions panel: the recorded sessions' Market Profiles side by side, oldest left,
 * with a composite of the newest few at the right and a table of each session's levels. It reaches
 * further back (up to 20 sessions) than the candles over it, which load at most seven days. Reads the
 * bars the `profile-bars` job keeps (`/api/charts/profile-bars`); the profiles are built here by the
 * mirror of `cherrypick.core.marketprofile`. Chart only: no module reads it.
 */

const SESSION_CHOICES = ["5", "10", "20"] as const;
const COMPOSITE_CHOICES = ["off", "5", "10", "20"] as const;
const PLOT_HEIGHT = 560;
const AXIS_W = 64;
const GAP = 14;
const COMPOSITE_W = 170;
const LABEL_H = 34;
// Room above the highest row for its axis label.
const TOP_PAD = 8;
const NUM = { textAlign: "right", fontFamily: "var(--num-face)", fontVariantNumeric: "tabular-nums" } as const;

const DAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", day: "numeric" });
const MONTH_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const dayLabel = (iso: string): string => DAY.format(Date.parse(`${iso}T12:00:00Z`)).replace(/^(\d+) (\w+)$/, "$2 $1");
const monthDay = (iso: string): string => MONTH_DAY.format(Date.parse(`${iso}T12:00:00Z`));

function Toggle<T extends string>({
  value,
  options,
  onChange,
  label,
  render,
}: {
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
  render?: (v: T) => string;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o} type="button" className={value === o ? "mode-btn active" : "mode-btn"} onClick={() => onChange(o)}>
          {render ? render(o) : o}
        </button>
      ))}
    </div>
  );
}

const PROFILE_HELP: CardHelpContent = {
  title: "Reading the sessions side by side",
  body: (
    <>
      <HelpSection title="What each column is">
        <p>
          One regular session (09:30–16:00 ET), oldest on the left. Each 30-minute period is a letter, A to M, printed
          at every price it traded; pushed left, the letters make a histogram of time at price. The tinted rows are the
          value area (70% of the session's time), the amber row the point of control (POC), the price traded at
          longest. Row size is the same for every column so the rows line up; when the rows are too thin for letters
          they are drawn as bars.
        </p>
      </HelpSection>
      <HelpSection title="The composite">
        <p>
          The newest sessions merged into one profile: each row's time summed across them, then the same POC and
          value-area rules. Its POC and value area are also drawn as faint lines across every column, so each day can
          be read against the levels of the span.
        </p>
      </HelpSection>
      <HelpSection title="One session up close">
        <p>
          Click a column (or a row of the table) to open that session full size below the chart: every TPO letter,
          the first hour (A and B) in blue with its bracket, the POC in amber, single prints and tails marked, the
          open and close, and the session's facts beside it. Hover a row for its letters with their times and its
          volume. Click the same column again, or close, to put it away; the open session is kept in the address.
        </p>
      </HelpSection>
      <HelpSection title="Time or volume">
        <p>
          <strong>time</strong> draws each row by how long price spent there (the letters).{" "}
          <strong>volume</strong> draws it by how much traded there, with the volume POC in amber; the tinted value
          area stays the time-based one. Volume is spread evenly over each bar's range, as every profile built from
          candles is. SPX and NDX are indexes and carry no volume, so for them only time is offered.
        </p>
      </HelpSection>
      <HelpSection title="The table">
        <p>
          Each session's POC, value area, initial balance (the first hour's range) and full range. Value is higher
          when the session's value area sits wholly above the prior session's, lower when wholly below, overlapping
          when they share any price. A description of what traded, not a signal: no module reads it.
        </p>
      </HelpSection>
      <HelpSection title="Where the bars come from">
        <p>
          The <code>profile-bars</code> job saves 1-minute bars of SPX, SPY, NDX, QQQ, /ES and /NQ at 17:15 ET each
          trading day; the broker only ever hands back its newest 8,000, so history grows from the day the job
          started. A future is its active contract, named in the table. SPX and NDX carry no volume. Today,
          and any session the job has not recorded yet, is drawn from the live 15-minute candles and
          marked <em>live</em>; it moves as the session trades and is replaced by the recorded minutes after
          17:15 ET.
        </p>
      </HelpSection>
    </>
  ),
};

export type Measure = "time" | "volume";

/** The plot: a shared price axis, one column per session, the composite at the right. A column is a
 *  button: clicking it (or Enter on it) opens that session's detail below. */
function ProfilePlot({
  view,
  precision,
  measure,
  selected,
  onSelect,
}: {
  view: ProfileView;
  precision: number;
  measure: Measure;
  selected: string | null;
  onSelect: (session: string) => void;
}) {
  const { rowSize, profiles, composite } = view;
  const nRows = Math.round((view.high - view.low) / rowSize) + 1;
  const rowH = Math.max(3, Math.min(13, PLOT_HEIGHT / nRows));
  const letters = rowH >= 10 && measure === "time";
  const [boxRef, boxW] = useMeasure(0);
  const plotH = nRows * rowH;
  const y = (price: number): number => TOP_PAD + ((view.high - price) / rowSize) * rowH; // top of the row at `price`

  // Each column's natural width (its longest row at 8 px a letter, 4 a bar), then all of them and the
  // composite stretched by one factor to fill the card; the axis, gaps and margins stay as they are.
  const baseCell = letters ? 8 : 4;
  const natural = profiles.map((p) =>
    Math.max(partialPeriods(p) === null ? 44 : 60, Math.max(...p.rows.map((r) => r.letters.length)) * baseCell),
  );
  const compW0 = composite !== null ? COMPOSITE_W : 0;
  const fixed = AXIS_W + 8 + profiles.length * GAP + 6 + (composite !== null ? 10 : 0);
  const stretchable = natural.reduce((a, b) => a + b, 0) + compW0;
  const k = boxW > fixed && stretchable > 0 ? Math.max(1, (boxW - fixed) / stretchable) : 1;
  const cellW = baseCell * k;
  const compositeW = compW0 * k;

  let x = AXIS_W + 8;
  const columns = profiles.map((p, i) => {
    const w = natural[i]! * k;
    const col = { p, x, w };
    x += w + GAP;
    return col;
  });
  const compX = x + 6;
  const width = Math.floor(compX + (composite !== null ? compositeW + 10 : 0));
  const height = TOP_PAD + plotH + LABEL_H;
  const labelEvery = Math.max(1, Math.ceil(16 / rowH));
  const lineAcross = (price: number, key: string, stroke: string, dash: string) => (
    <line key={key} x1={AXIS_W} x2={width} y1={price} y2={price} stroke={stroke} strokeDasharray={dash} strokeWidth={1} opacity={0.55} />
  );

  return (
    <div ref={boxRef} style={{ overflowX: "auto", marginTop: 10 }}>
      <svg width={width} height={height} role="img" aria-label="Market Profile by session" style={{ display: "block" }}>
        {/* price axis */}
        {Array.from({ length: nRows }, (_, i) => i)
          .filter((i) => i % labelEvery === 0)
          .map((i) => {
            const price = view.high - i * rowSize;
            return (
              <text key={i} x={AXIS_W - 6} y={y(price) + rowH * 0.8} textAnchor="end" fontSize={10} fill="var(--text-muted)" fontFamily="var(--num-face)">
                {price.toFixed(precision)}
              </text>
            );
          })}
        {/* the composite's levels across every column */}
        {composite !== null && (
          <>
            {lineAcross(y(composite.poc) + rowH / 2, "cpoc", "var(--amber-line)", "6 4")}
            {lineAcross(y(composite.vah), "cvah", "var(--accent)", "2 4")}
            {lineAcross(y(composite.val) + rowH, "cval", "var(--accent)", "2 4")}
          </>
        )}
        {columns.map(({ p, x: cx, w }) => (
          <SessionColumn
            key={p.session}
            p={p}
            x={cx}
            w={w}
            y={y}
            rowH={rowH}
            cellW={cellW}
            letters={letters}
            plotH={TOP_PAD + plotH}
            live={view.live.includes(p.session)}
            measure={measure}
            selected={selected === p.session}
            onSelect={onSelect}
          />
        ))}
        {composite !== null && <CompositeColumn c={composite} x={compX} w={compositeW} y={y} rowH={rowH} plotH={TOP_PAD + plotH} measure={measure} />}
      </svg>
    </div>
  );
}

/** A row's bar length as a share of the column's longest, by the chosen measure. */
const share = (value: number, max: number): number => (max > 0 ? value / max : 0);

function SessionColumn({
  p,
  x,
  w,
  y,
  rowH,
  cellW,
  letters,
  plotH,
  live,
  measure,
  selected,
  onSelect,
}: {
  p: MarketProfile;
  x: number;
  w: number;
  y: (price: number) => number;
  rowH: number;
  cellW: number;
  letters: boolean;
  plotH: number;
  live: boolean;
  measure: Measure;
  selected: boolean;
  onSelect: (session: string) => void;
}) {
  const byVolume = measure === "volume";
  const maxVol = Math.max(...p.rows.map((r) => r.volume));
  const peak = byVolume ? p.volumePoc : p.poc;
  const top = y(p.rows.at(-1)!.price);
  const bottom = y(p.rows[0]!.price) + rowH;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${dayLabel(p.session)} ${monthDay(p.session)}: open the session's detail`}
      aria-pressed={selected}
      onClick={() => onSelect(p.session)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(p.session);
        }
      }}
      style={{ cursor: "pointer", outline: "none" }}
    >
      <title>{`${dayLabel(p.session)} ${monthDay(p.session)} · POC ${p.poc} · click for the letters`}</title>
      {/* the whole column is the hit area, empty rows included */}
      <rect x={x - 5} y={TOP_PAD} width={w + 10} height={plotH - TOP_PAD + LABEL_H - 4} fill="transparent" />
      {selected && <rect x={x - 5} y={top - 3} width={w + 10} height={bottom - top + 6} fill="none" stroke="var(--accent)" rx={3} />}
      <rect x={x - 3} y={y(p.vah)} width={w + 6} height={y(p.val) - y(p.vah) + rowH} fill="var(--accent)" opacity={0.16} />
      {p.rows.map((r) => {
        const n = r.letters.length;
        if (n === 0) return null;
        const rowTop = y(r.price);
        const hot = r.price === peak;
        if (letters) {
          return (
            <text key={r.price} x={x} y={rowTop + rowH * 0.82} fontSize={rowH * 0.92} fontFamily="var(--num-face)" fill={hot ? "var(--amber-line)" : "var(--text)"} fontWeight={hot ? 700 : 400}>
              {r.letters.split("").map((ch, i) => (
                <tspan key={i} x={x + i * cellW}>
                  {ch}
                </tspan>
              ))}
            </text>
          );
        }
        const len = byVolume ? share(r.volume, maxVol) * w : n * cellW;
        if (len <= 0) return null;
        return (
          <rect
            key={r.price}
            x={x}
            y={rowTop + 0.5}
            width={Math.max(1, len)}
            height={Math.max(1, rowH - 1)}
            fill={hot ? "var(--amber-line)" : byVolume ? "var(--accent)" : "var(--text-muted)"}
            opacity={hot ? 0.95 : 0.7}
          />
        );
      })}
      <text x={x} y={plotH + 14} fontSize={11} fill={selected ? "var(--accent)" : "var(--text)"} fontWeight={selected ? 700 : 400}>
        {dayLabel(p.session)}
      </text>
      <text x={x} y={plotH + 27} fontSize={10} fill={live ? "var(--accent)" : "var(--text-muted)"}>
        {live ? "live" : monthDay(p.session).split(" ")[0]}
        {partialPeriods(p) !== null && ` ${partialPeriods(p)}`}
      </text>
    </g>
  );
}

function CompositeColumn({
  c,
  x,
  w,
  y,
  rowH,
  plotH,
  measure,
}: {
  c: MarketComposite;
  x: number;
  w: number;
  y: (price: number) => number;
  rowH: number;
  plotH: number;
  measure: Measure;
}) {
  const byVolume = measure === "volume";
  const value = (r: MarketComposite["rows"][number]): number => (byVolume ? r.volume : r.tpo);
  const max = Math.max(...c.rows.map(value));
  const peak = byVolume ? c.volumePoc : c.poc;
  return (
    <g>
      <rect x={x - 3} y={y(c.vah)} width={w + 6} height={y(c.val) - y(c.vah) + rowH} fill="var(--accent)" opacity={0.16} />
      {c.rows.map((r) =>
        value(r) <= 0 ? null : (
          <rect
            key={r.price}
            x={x}
            y={y(r.price) + 0.5}
            width={Math.max(1, share(value(r), max) * w)}
            height={Math.max(1, rowH - 1)}
            fill={r.price === peak ? "var(--amber-line)" : "var(--accent)"}
            opacity={r.price === peak ? 0.95 : 0.55}
          />
        ),
      )}
      <text x={x} y={plotH + 14} fontSize={11} fill="var(--text)">
        composite
      </text>
      <text x={x} y={plotH + 27} fontSize={10} fill="var(--text-muted)">
        {c.sessions.length} sessions{byVolume ? " · volume" : ""}
      </text>
    </g>
  );
}

function LevelsTable({
  view,
  contracts,
  liveContract,
  precision,
  future,
  selected,
  onSelect,
}: {
  view: ProfileView;
  contracts: Record<string, string>;
  liveContract: string;
  precision: number;
  future: boolean;
  selected: string | null;
  onSelect: (session: string) => void;
}) {
  const f = (v: number | null): string => (v === null ? "—" : v.toFixed(precision));
  const rows = [...view.profiles].reverse();
  return (
    <div className="table-scroll" style={{ marginTop: 12 }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>session</th>
            {future && <th>contract</th>}
            <th style={NUM}>POC</th>
            <th style={NUM}>value area</th>
            <th>value vs prior</th>
            <th style={NUM}>initial balance</th>
            <th style={NUM}>range</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const i = view.profiles.indexOf(p);
            const prior = i > 0 ? view.profiles[i - 1]! : null;
            return (
              <tr
                key={p.session}
                onClick={() => onSelect(p.session)}
                style={{ cursor: "pointer", background: selected === p.session ? "var(--surface-raised)" : undefined }}
                aria-selected={selected === p.session}
              >
                <td>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(p.session);
                    }}
                    style={{ background: "none", border: 0, padding: 0, color: "inherit", font: "inherit", cursor: "pointer", fontWeight: selected === p.session ? 700 : 400 }}
                  >
                    {dayLabel(p.session)} {monthDay(p.session).split(" ")[0]}
                  </button>
                  {partialPeriods(p) !== null && (
                    <span className="muted" title="The session's bars cover only these 30-minute periods (A is 09:30, M is 15:30).">
                      {" "}
                      · periods {partialPeriods(p)} only
                    </span>
                  )}
                  {view.live.includes(p.session) && (
                    <span style={{ color: "var(--accent)" }} title="From the live candles; the profile-bars job records it at 17:15 ET.">
                      {" "}
                      · live
                    </span>
                  )}
                </td>
                {future && (
                  <td className="muted">{view.live.includes(p.session) ? liveContract : (contracts[p.session] ?? "—")}</td>
                )}
                <td style={NUM}>{f(p.poc)}</td>
                <td style={NUM}>
                  {f(p.val)}–{f(p.vah + p.rowSize)}
                </td>
                <td>{prior === null ? <span className="muted">—</span> : valueRelation(prior, p)}</td>
                <td style={NUM}>{p.ibLow === null || p.ibHigh === null ? "—" : `${f(p.ibLow)}–${f(p.ibHigh)}`}</td>
                <td style={NUM}>
                  {f(p.low)}–{f(p.high)} ({(p.high - p.low).toFixed(precision)})
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Whether the profile-bars job records a Charts symbol, and so whether the chart has a sessions panel. */
export const isProfileSymbol = (s: string | null): s is ProfileSymbol => s !== null && (PROFILE_SYMBOLS as readonly string[]).includes(s);
const pick = <T extends string>(raw: string | null, options: readonly T[], fallback: T): T =>
  raw !== null && (options as readonly string[]).includes(raw) ? (raw as T) : fallback;

/**
 * The chart's sessions panel, under the intraday view for a recorded symbol: the page's symbol, and its
 * own keys `days` (how many recorded sessions, side by side), `composite`, `measure` and `day` (the
 * session whose letters are open). `days`, not `sessions`: that key is the chart's histogram count.
 */
export function SessionsPanel({ symbol }: { symbol: ProfileSymbol }) {
  const [params, setParams] = useSearchParams();
  const sessions = pick(params.get("days"), SESSION_CHOICES, "10");
  const compositeChoice = pick(params.get("composite"), COMPOSITE_CHOICES, "5");
  const set = (key: string, value: string): void => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const { data, isError } = useProfileBars(symbol, Number(sessions));
  // The live candles, for the sessions the store does not hold yet: a cash symbol is its own DXLink
  // symbol, a future the futures map's contract (none until the map has loaded).
  const ticker = useFuturesTicker();
  const liveSymbol = symbol.startsWith("/")
    ? (ticker.data?.entries.find((e) => `/${e.product}` === symbol)?.streamerSymbol ?? "")
    : symbol;
  const liveBars = useCandles(liveSymbol, "15m");
  const compositeN = compositeChoice === "off" ? 0 : Number(compositeChoice);
  const view = useMemo(
    () => (data === undefined ? null : buildView(data, compositeN, liveBars)),
    [data, compositeN, liveBars],
  );
  const precision = 2;
  // Volume at price exists only where the symbol carries volume: never SPX or NDX.
  const hasVolume = view !== null && view.profiles.some((p) => p.volumeTotal > 0);
  const measure: Measure = hasVolume && params.get("measure") === "volume" ? "volume" : "time";
  // The session whose detail is open (`?day=`), clicking its column or row again closes it.
  const day = params.get("day");
  const selected = view?.profiles.find((p) => p.session === day);
  const select = (session: string): void => {
    const next = new URLSearchParams(params);
    if (session === day) next.delete("day");
    else next.set("day", session);
    setParams(next, { replace: true });
  };

  return (
    <section className="card">
      <div className="card-head">
        <h2>{symbol} · sessions side by side</h2>
        <CardHelpButton cardTitle="Sessions side by side" help={PROFILE_HELP} />
        <span className="chip">chart only</span>
        <div style={{ marginLeft: "auto", display: "flex", flexWrap: "wrap", gap: 8, minWidth: 0 }}>
          <Toggle value={sessions} options={SESSION_CHOICES} onChange={(v) => set("days", v)} label="recorded sessions" render={(v) => `${v} days`} />
          <Toggle
            value={compositeChoice}
            options={COMPOSITE_CHOICES}
            onChange={(v) => set("composite", v)}
            label="composite"
            render={(v) => (v === "off" ? "no composite" : `composite ${v}`)}
          />
          <div className="mode-toggle" role="group" aria-label="measure">
            {(["time", "volume"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={measure === m ? "mode-btn active" : "mode-btn"}
                disabled={m === "volume" && !hasVolume}
                title={
                  m === "volume" && !hasVolume
                    ? `${symbol} is an index and has no traded volume; pick SPY, QQQ, /ES or /NQ`
                    : `bars by ${m} at price`
                }
                onClick={() => set("measure", m)}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>
      {data === undefined ? (
        <p className="muted">{isError ? "Could not read the profile bars from the console server." : "loading…"}</p>
      ) : view === null ? (
        <p className="muted">{data.reason ?? "No regular-session bars to profile."}</p>
      ) : (
        <>
          {!data.available && <p className="muted">{data.reason} Showing the live candles only.</p>}
          <div className="intraday-status muted">
            {monthDay(view.profiles[0]!.session)} – {monthDay(view.profiles.at(-1)!.session)} · {view.profiles.length} sessions · rows of{" "}
            {view.rowSize}
            {view.live.length > 0 && <> · {view.live.map(monthDay).join(", ")} from the live candles</>}
            {view.composite !== null && (
              <>
                {" "}
                · composite POC <strong>{view.composite.poc.toFixed(precision)}</strong>, VA {view.composite.val.toFixed(precision)}–
                {(view.composite.vah + view.rowSize).toFixed(precision)}
              </>
            )}
            {data.fetchedAt !== null && <> · recorded {new Date(data.fetchedAt).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })} ET</>}
          </div>
          {!hasVolume && (
            <p className="muted" style={{ fontSize: "0.85em", margin: "4px 0 0" }}>
              Volume view is off for {symbol}: it is an index and has no traded volume. Pick SPY, QQQ, /ES or /NQ to see
              volume at price.
            </p>
          )}
          <ProfilePlot view={view} precision={precision} measure={measure} selected={selected?.session ?? null} onSelect={select} />
          {selected !== undefined && (
            <SessionDetail
              p={selected}
              precision={precision}
              live={view.live.includes(selected.session)}
              onClose={() => select(selected.session)}
            />
          )}
          <LevelsTable
            view={view}
            contracts={data.contracts}
            liveContract={liveSymbol || "—"}
            precision={precision}
            future={symbol.startsWith("/")}
            selected={selected?.session ?? null}
            onSelect={select}
          />
        </>
      )}
    </section>
  );
}
