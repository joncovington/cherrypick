import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { PROFILE_SYMBOLS, type ProfileSymbol } from "@console/shared";
import { useFuturesTicker, useProfileBars } from "../../lib/api";
import { useCandles } from "../../lib/useCandles";
import { CardHelpButton, HelpSection, type CardHelpContent } from "../../components/CardHelp";
import type { MarketComposite, MarketProfile } from "../../lib/marketProfile";
import { buildView, partialPeriods, valueRelation, type ProfileView } from "./profileView";

/**
 * Charts → profile: the recorded sessions' Market Profiles side by side, oldest left, with a composite
 * of the newest few at the right and a table of each session's levels. Reads the bars the
 * `profile-bars` job keeps (`/api/charts/profile-bars`); the profiles are built here by the mirror of
 * `cherrypick.core.marketprofile`. Chart only: no module reads it.
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
  title: "Reading the profile tab",
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

/** The plot: a shared price axis, one column per session, the composite at the right. */
function ProfilePlot({ view, precision }: { view: ProfileView; precision: number }) {
  const { rowSize, profiles, composite } = view;
  const nRows = Math.round((view.high - view.low) / rowSize) + 1;
  const rowH = Math.max(3, Math.min(13, PLOT_HEIGHT / nRows));
  const letters = rowH >= 10;
  const cellW = letters ? 8 : 4;
  const plotH = nRows * rowH;
  const y = (price: number): number => TOP_PAD + ((view.high - price) / rowSize) * rowH; // top of the row at `price`

  let x = AXIS_W + 8;
  const columns = profiles.map((p) => {
    const maxTpo = Math.max(...p.rows.map((r) => r.letters.length));
    const w = Math.max(partialPeriods(p) === null ? 44 : 60, maxTpo * cellW);
    const col = { p, x, w };
    x += w + GAP;
    return col;
  });
  const compX = x + 6;
  const width = compX + (composite !== null ? COMPOSITE_W + 10 : 0);
  const height = TOP_PAD + plotH + LABEL_H;
  const labelEvery = Math.max(1, Math.ceil(16 / rowH));
  const lineAcross = (price: number, key: string, stroke: string, dash: string) => (
    <line key={key} x1={AXIS_W} x2={width} y1={price} y2={price} stroke={stroke} strokeDasharray={dash} strokeWidth={1} opacity={0.55} />
  );

  return (
    <div style={{ overflowX: "auto", marginTop: 10 }}>
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
          />
        ))}
        {composite !== null && <CompositeColumn c={composite} x={compX} y={y} rowH={rowH} plotH={TOP_PAD + plotH} />}
      </svg>
    </div>
  );
}

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
}) {
  return (
    <g>
      <rect x={x - 3} y={y(p.vah)} width={w + 6} height={y(p.val) - y(p.vah) + rowH} fill="var(--accent)" opacity={0.16} />
      {p.rows.map((r) => {
        const n = r.letters.length;
        if (n === 0) return null;
        const top = y(r.price);
        const poc = r.price === p.poc;
        if (letters) {
          return (
            <text key={r.price} x={x} y={top + rowH * 0.82} fontSize={rowH * 0.92} fontFamily="var(--num-face)" fill={poc ? "var(--amber-line)" : "var(--text)"} fontWeight={poc ? 700 : 400}>
              {r.letters.split("").map((ch, i) => (
                <tspan key={i} x={x + i * cellW}>
                  {ch}
                </tspan>
              ))}
            </text>
          );
        }
        return (
          <rect key={r.price} x={x} y={top + 0.5} width={n * cellW} height={Math.max(1, rowH - 1)} fill={poc ? "var(--amber-line)" : "var(--text-muted)"} opacity={poc ? 0.95 : 0.7} />
        );
      })}
      <text x={x} y={plotH + 14} fontSize={11} fill="var(--text)">
        {dayLabel(p.session)}
      </text>
      <text x={x} y={plotH + 27} fontSize={10} fill={live ? "var(--accent)" : "var(--text-muted)"}>
        {live ? "live" : monthDay(p.session).split(" ")[0]}
        {partialPeriods(p) !== null && ` ${partialPeriods(p)}`}
      </text>
    </g>
  );
}

function CompositeColumn({ c, x, y, rowH, plotH }: { c: MarketComposite; x: number; y: (price: number) => number; rowH: number; plotH: number }) {
  const maxTpo = Math.max(...c.rows.map((r) => r.tpo));
  return (
    <g>
      <rect x={x - 3} y={y(c.vah)} width={COMPOSITE_W + 6} height={y(c.val) - y(c.vah) + rowH} fill="var(--accent)" opacity={0.16} />
      {c.rows.map((r) =>
        r.tpo === 0 ? null : (
          <rect
            key={r.price}
            x={x}
            y={y(r.price) + 0.5}
            width={Math.max(1, (r.tpo / maxTpo) * COMPOSITE_W)}
            height={Math.max(1, rowH - 1)}
            fill={r.price === c.poc ? "var(--amber-line)" : "var(--accent)"}
            opacity={r.price === c.poc ? 0.95 : 0.55}
          />
        ),
      )}
      <text x={x} y={plotH + 14} fontSize={11} fill="var(--text)">
        composite
      </text>
      <text x={x} y={plotH + 27} fontSize={10} fill="var(--text-muted)">
        {c.sessions.length} sessions
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
}: {
  view: ProfileView;
  contracts: Record<string, string>;
  liveContract: string;
  precision: number;
  future: boolean;
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
              <tr key={p.session}>
                <td>
                  {dayLabel(p.session)} {monthDay(p.session).split(" ")[0]}
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

const isSymbol = (s: string | null): s is ProfileSymbol => s !== null && (PROFILE_SYMBOLS as readonly string[]).includes(s);
const pick = <T extends string>(raw: string | null, options: readonly T[], fallback: T): T =>
  raw !== null && (options as readonly string[]).includes(raw) ? (raw as T) : fallback;

export function ProfilePage() {
  const [params, setParams] = useSearchParams();
  const rawSymbol = params.get("symbol");
  const symbol: ProfileSymbol = isSymbol(rawSymbol) ? rawSymbol : "SPX";
  const sessions = pick(params.get("sessions"), SESSION_CHOICES, "10");
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

  return (
    <section className="card">
      <div className="card-head">
        <h2>{symbol} · market profile by session</h2>
        <CardHelpButton cardTitle="Market profile" help={PROFILE_HELP} />
        <span className="chip">chart only</span>
        <div style={{ marginLeft: "auto", display: "flex", flexWrap: "wrap", gap: 8, minWidth: 0 }}>
          <Toggle value={symbol} options={PROFILE_SYMBOLS} onChange={(v) => set("symbol", v)} label="symbol" />
          <Toggle value={sessions} options={SESSION_CHOICES} onChange={(v) => set("sessions", v)} label="sessions" render={(v) => `${v} days`} />
          <Toggle
            value={compositeChoice}
            options={COMPOSITE_CHOICES}
            onChange={(v) => set("composite", v)}
            label="composite"
            render={(v) => (v === "off" ? "no composite" : `composite ${v}`)}
          />
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
          <ProfilePlot view={view} precision={precision} />
          <LevelsTable
            view={view}
            contracts={data.contracts}
            liveContract={liveSymbol || "—"}
            precision={precision}
            future={symbol.startsWith("/")}
          />
        </>
      )}
    </section>
  );
}
