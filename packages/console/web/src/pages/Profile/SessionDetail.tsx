import { LETTERS, SESSION_OPEN_MIN, PERIOD_MINUTES, type MarketProfile } from "../../lib/marketProfile";

/**
 * One session's profile at full size: the TPO letters on every row, the value area tinted, the POC row
 * in amber, the initial balance (periods A and B) as a bracket and in the accent colour, single prints
 * and tails marked, the open and close, and each row's letters, times and volume on hover. Opened by
 * clicking a session's column in the chart's sessions panel. Everything here is read off the profile the column
 * already drew (`lib/marketProfile`); nothing is computed that the column did not have.
 */

const ROW_H = 15;
const CELL_W = 11;
const AXIS_W = 70;
// The margin between the prices and the letters: the IB bracket, then the single-print/tail marker, then
// the open marker, each in its own lane.
const MARK_W = 36;
const PAD = 8;

const DAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "long", month: "short", day: "numeric" });

/** A period letter's start on the ET clock: A is 09:30, each next letter thirty minutes on. */
export function periodStart(letter: string): string {
  const i = LETTERS.indexOf(letter);
  const m = SESSION_OPEN_MIN + i * PERIOD_MINUTES;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function SessionDetail({
  p,
  precision,
  live,
  onClose,
}: {
  p: MarketProfile;
  precision: number;
  live: boolean;
  onClose: () => void;
}) {
  const f = (v: number): string => v.toFixed(precision);
  const rows = [...p.rows].reverse(); // high to low
  const maxTpo = Math.max(...p.rows.map((r) => r.letters.length));
  const singles = new Set(p.singlePrints);
  const tailTop = new Set(p.rows.slice(p.rows.length - p.tailHigh).map((r) => r.price));
  const tailBottom = new Set(p.rows.slice(0, p.tailLow).map((r) => r.price));
  const lettersX = AXIS_W + MARK_W;
  const width = lettersX + Math.max(13, maxTpo) * CELL_W + PAD * 2;
  const height = rows.length * ROW_H + PAD * 2;
  const y = (i: number): number => PAD + i * ROW_H;
  const rowIndex = (price: number): number => rows.findIndex((r) => price >= r.price && price < r.price + p.rowSize);
  const vaTop = rows.findIndex((r) => r.price === p.vah);
  const vaBottom = rows.findIndex((r) => r.price === p.val);
  const ibTop = p.ibHigh === null ? -1 : rowIndex(p.ibHigh);
  const ibBottom = p.ibLow === null ? -1 : rowIndex(p.ibLow);
  const openRow = rowIndex(p.open);
  const closeRow = rowIndex(p.close);

  const facts: Array<[string, string]> = [
    ["POC", f(p.poc)],
    ["value area", `${f(p.val)}–${f(p.vah + p.rowSize)}`],
    ["initial balance", p.ibLow === null || p.ibHigh === null ? "—" : `${f(p.ibLow)}–${f(p.ibHigh)} (${(p.ibHigh - p.ibLow).toFixed(precision)})`],
    ["range", `${f(p.low)}–${f(p.high)} (${(p.high - p.low).toFixed(precision)})`],
    [
      "beyond the IB",
      p.extensionUp === null || p.extensionDown === null
        ? "—"
        : `up ${p.extensionUp.toFixed(precision)} · down ${p.extensionDown.toFixed(precision)}`,
    ],
    ["open / close", `${f(p.open)} / ${f(p.close)}`],
    ["high", p.poorHigh ? "poor (two or more TPOs at the top row)" : p.tailHigh >= 2 ? `tail, ${p.tailHigh} rows` : "single row"],
    ["low", p.poorLow ? "poor (two or more TPOs at the bottom row)" : p.tailLow >= 2 ? `tail, ${p.tailLow} rows` : "single row"],
    ["single prints", p.singlePrints.length === 0 ? "none" : `${p.singlePrints.length} row(s)`],
    ["volume POC", p.volumePoc === null ? "— (no volume)" : f(p.volumePoc)],
    ["periods", p.periods.length > 1 ? `${p.periods[0]}–${p.periods.at(-1)} (${p.tpoTotal} TPOs, rows of ${p.rowSize})` : p.periods],
  ];

  return (
    <section aria-label={`profile of ${p.session}`} style={{ marginTop: 14, border: "1px solid var(--border)", borderRadius: 6, padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <strong>{DAY.format(Date.parse(`${p.session}T12:00:00Z`))}</strong>
        {live && <span style={{ color: "var(--accent)" }}>live</span>}
        <span className="muted" style={{ fontSize: "0.85em" }}>
          hover a row for its letters, times and volume
        </span>
        <button type="button" className="mode-btn" onClick={onClose} style={{ marginLeft: "auto" }} aria-label="Close the session detail">
          close
        </button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 18, marginTop: 8, alignItems: "flex-start" }}>
        <div style={{ overflowX: "auto", minWidth: 0 }}>
          <svg width={width} height={height} role="img" aria-label={`TPO letters for ${p.session}`} style={{ display: "block" }}>
            {vaTop >= 0 && vaBottom >= 0 && (
              <rect x={lettersX - 4} y={y(vaTop)} width={width - lettersX - PAD + 4} height={(vaBottom - vaTop + 1) * ROW_H} fill="var(--accent)" opacity={0.14} />
            )}
            {ibTop >= 0 && ibBottom >= 0 && (
              <rect x={AXIS_W + 6} y={y(ibTop) + 2} width={4} height={(ibBottom - ibTop + 1) * ROW_H - 4} rx={2} fill="var(--accent)">
                <title>initial balance (A and B)</title>
              </rect>
            )}
            {rows.map((r, i) => {
              const poc = r.price === p.poc;
              const single = singles.has(r.price);
              const tail = tailTop.has(r.price) || tailBottom.has(r.price);
              const times = r.letters
                .split("")
                .map((ch) => `${ch} ${periodStart(ch)}`)
                .join(", ");
              return (
                <g key={r.price}>
                  <title>
                    {`${f(r.price)}–${f(r.price + p.rowSize)} · ${r.letters.length} TPO${r.letters.length === 1 ? "" : "s"}${times ? `: ${times}` : ""}${r.volume > 0 ? ` · volume ${Math.round(r.volume).toLocaleString("en-US")}` : ""}${poc ? " · POC" : ""}${single ? " · single print" : ""}${tail ? " · tail" : ""}`}
                  </title>
                  <rect x={0} y={y(i)} width={width} height={ROW_H} fill="transparent" />
                  {poc && <rect x={lettersX - 4} y={y(i) + 1} width={width - lettersX - PAD + 4} height={ROW_H - 2} fill="none" stroke="var(--amber-line)" rx={2} />}
                  <text x={AXIS_W - 6} y={y(i) + ROW_H * 0.75} textAnchor="end" fontSize={10.5} fill="var(--text-muted)" fontFamily="var(--num-face)">
                    {f(r.price)}
                  </text>
                  {(single || tail) && (
                    <rect x={AXIS_W + 14} y={y(i) + 4} width={6} height={ROW_H - 8} rx={1} fill={tail ? "var(--neg, #ee6760)" : "var(--amber-line)"} opacity={0.8} />
                  )}
                  {i === openRow && (
                    <text x={lettersX - 11} y={y(i) + ROW_H * 0.75} fontSize={11} fill="var(--accent)">
                      ▸
                    </text>
                  )}
                  {r.letters.split("").map((ch, k) => (
                    <text
                      key={k}
                      x={lettersX + k * CELL_W}
                      y={y(i) + ROW_H * 0.76}
                      fontSize={12}
                      fontFamily="var(--num-face)"
                      fontWeight={poc ? 700 : 400}
                      fill={poc ? "var(--amber-line)" : ch === "A" || ch === "B" ? "var(--accent)" : "var(--text)"}
                    >
                      {ch}
                    </text>
                  ))}
                  {i === closeRow && (
                    <text x={lettersX + r.letters.length * CELL_W + 4} y={y(i) + ROW_H * 0.75} fontSize={11} fill="var(--accent)">
                      ◂
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 14px", margin: 0, fontSize: "0.88em", minWidth: 220, flex: "1 1 260px" }}>
          {facts.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt className="muted">{k}</dt>
              <dd style={{ margin: 0, fontFamily: "var(--num-face)", fontVariantNumeric: "tabular-nums" }}>{v}</dd>
            </div>
          ))}
          <dt className="muted">key</dt>
          <dd style={{ margin: 0 }}>
            <span style={{ color: "var(--accent)" }}>A B</span> the first hour · <span style={{ color: "var(--amber-line)" }}>amber</span> POC
            and single prints · <span style={{ color: "var(--neg, #ee6760)" }}>red</span> tail rows · ▸ open · ◂ close
          </dd>
        </dl>
      </div>
    </section>
  );
}
