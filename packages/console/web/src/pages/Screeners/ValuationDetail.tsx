import type { CSSProperties, ReactNode } from "react";
import type { ValuationModel, ValuationPayload, ValuationRow } from "@console/shared";
import { AXIS_FONT, CHART_GRID, CHART_GRID_STRONG, niceTicks } from "../../components/Charts";
import { useMeasure } from "../../lib/useMeasure";

/**
 * What opens beneath a fair-value row (2026-10-28, the user's choice of what to show): a sentence on
 * what the price needs, two dials (stretch, and the model's value against the price), the grid's range
 * with the price on it, the forecast's free cash flow, the arithmetic from the business's value to
 * the value per share, and the assumptions. Every figure is the builder's; this draws them.
 *
 * The value-vs-price dial is the figure tested 2018-26 with no edge as a selector, so it says so
 * beneath itself and wears no good/bad colour; the stretch dial is the agreed read (how much has to
 * go right, for a PMCC long leg or a stock a wheel or skate put may be assigned), so its two zones
 * are named in words beside their colour. The range is the grid's (cost of capital a point either
 * way, long-run growth half a point either way), never an outlook case: one base case, by design.
 */

const DASH = "—";

const FLAG_TEXT: Record<string, string> = {
  terminal_heavy: "Over 85% of the value is the perpetuity after year ten, so the answer rests on the long-run guess.",
  wacc_floored: "The cost of capital came out under 7% and was floored there.",
  split_adjusted: "The stock split after its last annual report; the share count was carried through the split.",
  negative_base_fcf: "The first forecast year's cash flow is negative.",
  beta_bounded: "The measured beta sat outside 0.5–2.0 and was bounded.",
  beta_disagrees: "The broker's beta differs from ours by more than 0.5.",
  shares_stale: "Price × year-end shares is more than 10% off the broker's market cap: the share count may be stale.",
  // Version 2's (2026-10-29).
  consensus_dip:
    "An analyst year grows less than the long-run rate (a patent cliff, a cycle turning); the forecast fades from it, so if the dip is a one-off the value is understated.",
  filing_disagrees: "A line in the filing differs from Dolt's by more than 10%; the value uses the filing (see Advanced).",
  wc_floored:
    "Working capital is negative (customers and suppliers fund the business); the forecast takes no cash from it as revenue grows.",
  negative_early_fcf: "The forecast's free cash flow is negative in one of its first three years (heavy investment ahead).",
  erp_fallback: "Damodaran's equity risk premium file was missing; a fallback premium was used.",
};

export function flagText(f: string): string {
  return FLAG_TEXT[f] ?? f.replace(/_/g, " ");
}

const STATE_LABEL: Record<string, string> = {
  valued: "valued",
  "not-applicable": "not applicable",
  "no-statements": "no statements",
  refused: "no value",
};

export function stateLabel(s: string): string {
  return STATE_LABEL[s] ?? s;
}

/** Why there is no value, in words: the builder's and the model's reasons (`core.valuation`). */
const REASON_TEXT: Record<string, string> = {
  fund: "A fund has no operating cash flow of its own to discount.",
  financials: "A bank earns on its balance sheet, which a cash-flow model does not describe.",
  "real estate": "A REIT is valued on its assets, which a cash-flow model does not describe.",
  debt_dominated:
    "Its operating profit covers its interest less than 1.5 times and debt is over half its capital. The model prices debt at its coupon, far below what a lender would charge such a company today, so it shows no value rather than a wrong one.",
  negative_equity: "After the debt, nothing is left for the shareholders.",
  non_positive_ev: "The forecast cash flows are worth nothing at the model's discount rate.",
  negative_terminal_fcf: "The forecast cash flow is still negative in year ten, so there is no perpetuity to value.",
  no_operating_profit: "The company has no operating profit to forecast from.",
  no_revenue: "The statements carry no revenue.",
  too_few_years: "Fewer than three fiscal years of statements: not enough history for the medians.",
  no_growth_read: "No revenue growth could be read from the history.",
  wacc_near_growth: "The discount rate sits too close to the long-run growth for a perpetuity to mean anything.",
  no_beta: "Too little price history to measure the stock's beta.",
  no_shares: "The statements carry no share count.",
  no_price: "No closing price on or before the session.",
};

export function reasonText(reason: string | null): string {
  if (reason === null) return "The model has no value for it.";
  return REASON_TEXT[reason] ?? `${reason.replace(/_/g, " ")}.`;
}

export function money(v: number | null): string {
  return v === null ? DASH : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function pct(v: number | null, digits = 1): string {
  return v === null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

export function signedPct(v: number | null): string {
  if (v === null) return DASH;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
}

/** Whole-company dollars, short: $1.38T, $393B, $10.8B, $950M. */
export function big(v: number | null): string {
  if (v === null) return DASH;
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e11) return `${sign}$${(a / 1e9).toFixed(0)}B`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
  return `${sign}$${a.toFixed(0)}`;
}

function count(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString();
}

export function needsGrowth(r: ValuationRow): string {
  if (r.impliedGrowth === null) return DASH;
  if (r.impliedGrowthEdge === "above") return `over ${signedPct(r.impliedGrowth)}`;
  if (r.impliedGrowthEdge === "below") return `under ${signedPct(r.impliedGrowth)}`;
  return signedPct(r.impliedGrowth);
}

/** The margin the price needs over the company's best of the last five fiscal years: 0.8 asks 80% of
 *  its best year, 1.5 half again above anything it has done. Infinity when no margin under 100% of
 *  revenue reaches the price; null when there is no valued row or no positive best to measure against. */
export function stretch(r: ValuationRow): number | null {
  if (r.state !== "valued" || r.bestMargin === null || r.bestMargin <= 0) return null;
  return r.impliedMargin === null ? Number.POSITIVE_INFINITY : r.impliedMargin / r.bestMargin;
}

export function stretchText(v: number | null): string {
  if (v === null) return DASH;
  return v === Number.POSITIVE_INFINITY ? "none reaches" : `${v.toFixed(2)}×`;
}

/** The model's value against the price: positive when the value sits above it. The gap the builder
 *  writes is the other way round (price over value), so this is its reciprocal, never a new figure. */
export function valueVsPrice(r: ValuationRow): number | null {
  if (r.fairValue === null || r.spot === null || r.spot <= 0) return null;
  return r.fairValue / r.spot - 1;
}

// ------------------------------------------------------------------------------------------- pieces

const CARD = {
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "10px 12px",
  background: "var(--surface)",
} as const;

function Card({ title, children, style }: { title: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ ...CARD, ...style }}>
      <div className="muted" style={{ fontSize: 11, letterSpacing: 0.4, textTransform: "uppercase", marginBottom: 6 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

/** A half-circle gauge from `min` to `max`, its arc split into named zones, a needle at `value`
 *  (clamped to the arc; the text says the real figure). */
export function Dial({
  value,
  min,
  max,
  zones,
  ticks,
  label,
}: {
  value: number | null;
  min: number;
  max: number;
  zones: Array<{ from: number; to: number; color: string }>;
  ticks: Array<{ at: number; text: string }>;
  label: string;
}) {
  const w = 220;
  const h = 128;
  const cx = w / 2;
  const cy = 108;
  const rad = 84;
  const ang = (v: number) => Math.PI * (1 - (Math.min(Math.max(v, min), max) - min) / (max - min));
  const pt = (v: number, r = rad) => [cx + r * Math.cos(ang(v)), cy - r * Math.sin(ang(v))] as const;
  const arc = (from: number, to: number) => {
    const [x0, y0] = pt(from);
    const [x1, y1] = pt(to);
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${rad} ${rad} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  };
  const finite = value !== null && Number.isFinite(value) ? value : value === null ? null : max;
  return (
    <svg viewBox={`-20 0 ${w + 40} ${h}`} role="img" aria-label={label} style={{ width: "100%", maxWidth: 280, height: "auto", display: "block" }}>
      <path d={arc(min, max)} fill="none" stroke="var(--chart-neutral)" strokeWidth={10} strokeLinecap="butt" opacity={0.35} />
      {zones.map((z) => (
        <path key={`${z.from}-${z.to}`} d={arc(z.from, z.to)} fill="none" stroke={z.color} strokeWidth={10} strokeLinecap="butt" />
      ))}
      {ticks.map((t) => {
        const [x, y] = pt(t.at, rad + 16);
        const [a, b] = pt(t.at, rad - 7);
        const [c, d] = pt(t.at, rad + 7);
        return (
          <g key={t.text}>
            <line x1={a} y1={b} x2={c} y2={d} stroke={CHART_GRID_STRONG} strokeWidth={1.5} />
            <text x={x} y={y + 3} textAnchor="middle" {...AXIS_FONT}>
              {t.text}
            </text>
          </g>
        );
      })}
      {finite !== null && (
        <g>
          <line x1={cx} y1={cy} x2={pt(finite, rad - 14)[0]} y2={pt(finite, rad - 14)[1]} stroke="var(--text)" strokeWidth={2.5} strokeLinecap="round" />
          <circle cx={cx} cy={cy} r={5} fill="var(--text)" />
        </g>
      )}
    </svg>
  );
}

/** The grid's low, base and high on one line, with the price marked: where the price sits against
 *  the range a point of cost of capital and half a point of growth make. */
export function RangeBar({ low, base, high, price }: { low: number; base: number; high: number; price: number }) {
  // Drawn at the card's own width (`useMeasure`): a fixed viewBox stretched across a wide card
  // stretched its labels with it, to three times their size.
  const [ref, w] = useMeasure(720);
  const h = 100;
  const m = 110; // room for an end label that runs outward from its dot
  const lo = Math.min(low, price);
  const hi = Math.max(high, price);
  const pad = (hi - lo || 1) * 0.06;
  const X = (v: number) => m + ((v - (lo - pad)) / (hi - lo + pad * 2)) * (w - m * 2);
  const y = 34;
  // Three label rows, so a narrow range never stacks its labels: the base above the line; low and
  // high below it, each running away from the other; the price on a row of its own.
  return (
    <div ref={ref}>
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="fair value range and price" style={{ display: "block" }}>
      <line x1={m - 40} y1={y} x2={w - m + 40} y2={y} stroke={CHART_GRID_STRONG} strokeWidth={1} />
      <line x1={X(low)} y1={y} x2={X(high)} y2={y} stroke="var(--accent)" strokeWidth={6} strokeLinecap="round" opacity={0.25} />
      <circle cx={X(low)} cy={y} r={6} fill="var(--text-muted)" stroke="var(--surface)" strokeWidth={2} />
      <circle cx={X(high)} cy={y} r={6} fill="var(--text-muted)" stroke="var(--surface)" strokeWidth={2} />
      <text x={X(low) + 4} y={y + 22} textAnchor="end" {...AXIS_FONT} fontSize={11}>
        Low ${money(low)}
      </text>
      <text x={X(high) - 4} y={y + 22} textAnchor="start" {...AXIS_FONT} fontSize={11}>
        High ${money(high)}
      </text>
      <circle cx={X(base)} cy={y} r={8} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
      <text x={X(base)} y={y - 16} textAnchor="middle" {...AXIS_FONT} fontSize={12} fill="var(--text)" fontWeight={700}>
        Base ${money(base)}
      </text>
      <line x1={X(price)} y1={y - 12} x2={X(price)} y2={y + 12} stroke="var(--text)" strokeWidth={2.5} />
      <text x={X(price)} y={y + 44} textAnchor="middle" {...AXIS_FONT} fontSize={11} fill="var(--text)">
        Price ${money(price)}
      </text>
    </svg>
    </div>
  );
}

/** The forecast's free cash flow to the firm, one bar a year, labelled first and last. */
export function ForecastBars({ r }: { r: ValuationRow }) {
  const [ref, w] = useMeasure(560); // the card's width, as the range above
  const f = r.forecast ?? [];
  if (f.length === 0) return <p className="muted">No forecast in this file (built before 2026-10-28).</p>;
  const h = 200;
  const ml = 58;
  const mr = 10;
  const mt = 34;
  const mb = 22;
  const vals = f.map((y) => y.fcf);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const ticks = niceTicks(lo, hi, 4);
  const top = Math.max(hi, ...ticks);
  const bottom = Math.min(lo, ...ticks);
  const Y = (v: number) => mt + ((top - v) / (top - bottom || 1)) * (h - mt - mb);
  const bw = (w - ml - mr) / f.length;
  const first = r.consensusUsed ? "analyst consensus" : "five-year history";
  return (
    <div ref={ref}>
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="free cash flow forecast" style={{ display: "block" }}>
      <text x={ml + bw / 2} y={12} textAnchor="middle" {...AXIS_FONT}>
        Y1 growth: {first}
      </text>
      <text x={ml + bw * 1 + (bw * (f.length - 1)) / 2} y={12} textAnchor="middle" {...AXIS_FONT}>
        fades to the long run ({pct(r.terminalGrowth, 1)})
      </text>
      <path d={`M ${ml + bw + 6} 20 V 16 H ${w - mr - 6} V 20`} fill="none" stroke={CHART_GRID_STRONG} />
      {ticks.map((v) => (
        <g key={v}>
          <line x1={ml} y1={Y(v)} x2={w - mr} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? CHART_GRID_STRONG : CHART_GRID} />
          <text x={ml - 6} y={Y(v) + 3} textAnchor="end" {...AXIS_FONT}>
            {big(v)}
          </text>
        </g>
      ))}
      {f.map((y, i) => {
        const x = ml + i * bw + bw * 0.22;
        const bwid = bw * 0.56;
        const top0 = Math.min(Y(0), Y(y.fcf));
        const hgt = Math.max(Math.abs(Y(y.fcf) - Y(0)), 1);
        const labelled = i === 0 || i === f.length - 1;
        return (
          <g key={y.year}>
            <rect x={x} y={top0} width={bwid} height={hgt} rx={3} fill="var(--accent)">
              <title>{`Year ${y.year}: free cash flow ${big(y.fcf)} · revenue ${big(y.revenue)} (${signedPct(y.growth)}) · operating margin ${pct(y.ebitMargin)}`}</title>
            </rect>
            {labelled && (
              <text x={x + bwid / 2} y={(y.fcf >= 0 ? top0 : top0 + hgt + 12) - 5} textAnchor="middle" {...AXIS_FONT} fontSize={11} fill="var(--text)">
                {big(y.fcf)}
              </text>
            )}
            <text x={x + bwid / 2} y={h - 6} textAnchor="middle" {...AXIS_FONT}>
              Y{y.year}
            </text>
          </g>
        );
      })}
    </svg>
    </div>
  );
}

function Line({ label, value, strong, tone }: { label: ReactNode; value: string; strong?: boolean; tone?: "pos" | "neg" }) {
  return (
    <tr style={strong ? { fontWeight: 700 } : undefined}>
      <td>{label}</td>
      <td className={`num${tone === "pos" ? " pnl-pos" : tone === "neg" ? " pnl-neg" : ""}`} style={{ textAlign: "right", fontFamily: "var(--num-face)" }}>
        {value}
      </td>
    </tr>
  );
}

function signedBig(v: number): string {
  return v >= 0 ? `+${big(v)}` : big(v);
}

/** The sentence that opens the detail: the value against the price, and what the price needs. */
export function headline(r: ValuationRow): string {
  const vs = valueVsPrice(r);
  if (vs === null || r.fairValue === null || r.spot === null) return "";
  const where = `The model's value ($${money(r.fairValue)}) is ${Math.abs(vs * 100).toFixed(0)}% ${vs >= 0 ? "above" : "below"} today's price ($${money(r.spot)}).`;
  // The builder's reach decides; the gap only where an older file has none.
  if (r.reach === "below value" || (r.reach === null && vs >= 0)) return `${where} The price asks for nothing the business has not already done.`;
  const margin =
    r.impliedMargin === null
      ? "no operating margin under 100% of revenue gets there"
      : `it would need a long-run operating margin of ${pct(r.impliedMargin)} (the model uses ${pct(r.ebitMargin)}; its best in five years was ${pct(r.bestMargin)})`;
  const growth = r.impliedGrowth === null ? "" : `, or year-one revenue growth of ${needsGrowth(r)} (its best was ${signedPct(r.bestGrowth)})`;
  return `${where} To match the price, ${margin}${growth}, everything else held.`;
}

// -------------------------------------------------------------------------------------------- whole

export function ValuationDetail({ r, rule }: { r: ValuationRow; rule?: ValuationPayload["rule"] }) {
  if (r.state !== "valued") {
    return (
      <p style={{ margin: 0 }}>
        <strong>No fair value.</strong> {reasonText(r.reason)}
      </p>
    );
  }
  const s = stretch(r);
  const vs = valueVsPrice(r);
  const b = r.bridge;
  const beyond = r.reach === "beyond history";
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ ...CARD, borderLeft: `4px solid ${beyond ? "var(--warn)" : "var(--border)"}` }}>
        {beyond ? <strong>Beyond the company's record. </strong> : null}
        {headline(r)}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 10 }}>
        <Card title="Stretch">
          <Dial
            value={s}
            min={0}
            max={2}
            zones={[
              { from: 0, to: 1, color: "var(--pos)" },
              { from: 1, to: 2, color: "var(--warn)" },
            ]}
            ticks={[
              { at: 0, text: "0×" },
              { at: 1, text: "1×" },
              { at: 2, text: "2×" },
            ]}
            label={`stretch ${stretchText(s)}`}
          />
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: "var(--num-face)" }}>{stretchText(s)}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {s === null
              ? "no margin record to measure against"
              : s <= 1
                ? `inside its record: the price needs ${pct(r.impliedMargin)} against a best of ${pct(r.bestMargin)}`
                : `beyond its record: the price needs ${r.impliedMargin === null ? "more than any margin" : pct(r.impliedMargin)} against a best of ${pct(r.bestMargin)}`}
          </div>
        </Card>
        <Card title="Model value vs price">
          <Dial
            value={vs}
            min={-1}
            max={1}
            zones={[]}
            ticks={[
              { at: -1, text: "−100%" },
              { at: 0, text: "0" },
              { at: 1, text: "+100%" },
            ]}
            label={`model value vs price ${signedPct(vs)}`}
          />
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: "var(--num-face)" }}>{signedPct(vs)}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {vs === null ? DASH : vs >= 0 ? "the model's value sits above the price" : "the model's value sits below the price"}.{" "}
            <strong>Tested 2018–26: no edge</strong> as a way to pick names.
          </div>
        </Card>
        <Card title="Forecast inputs">
          <div style={{ fontSize: 15, fontWeight: 700 }}>{r.consensusUsed ? "Analyst consensus, then history" : "Company history"}</div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Year-one revenue growth {signedPct(r.growthY1)} from {r.consensusUsed ? "the current-year revenue consensus" : "the five-year growth"}, faded to{" "}
            {pct(r.terminalGrowth)} by year ten. Operating margin {pct(r.ebitMargin)}: the median of the last five years. Statements through{" "}
            {r.fiscalYearEnd ?? DASH}.
          </div>
        </Card>
      </div>

      {r.low !== null && r.high !== null && r.fairValue !== null && r.spot !== null && (
        <Card title="Valuation range">
          <RangeBar low={r.low} base={r.fairValue} high={r.high} price={r.spot} />
          <div className="muted" style={{ fontSize: 12 }}>
            Low and high move the cost of capital a point either way and the long-run growth half a point either way; the business forecast is the
            same in all three.
          </div>
        </Card>
      )}

      <Card title="Free cash flow forecast">
        <ForecastBars r={r} />
        <div className="muted" style={{ fontSize: 12 }}>
          Cash the business is expected to generate for all investors each year (free cash flow to the firm). Hover a bar for its revenue and margin.
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 10 }}>
        <Card title="From business value to value per share">
          {b === null ? (
            <p className="muted">Not in this file (built before 2026-10-28).</p>
          ) : (
            <table className="data-table" style={{ width: "100%" }}>
              <tbody>
                <Line label="Present value of years 1–10 cash flow" value={signedBig(b.pvForecast)} tone={b.pvForecast >= 0 ? "pos" : "neg"} />
                <Line label="Present value of cash flow after year 10" value={signedBig(b.pvTerminal)} tone={b.pvTerminal >= 0 ? "pos" : "neg"} />
                <Line label="= Enterprise value" value={big(b.ev)} strong />
                <Line label="Cash" value={signedBig(b.cash)} tone="pos" />
                <Line label="Debt" value={signedBig(-b.debt)} tone={b.debt > 0 ? "neg" : undefined} />
                <Line label="= Equity value" value={big(b.equity)} strong />
                <Line label={`÷ ${count(b.shares)} shares`} value={`$${money(r.fairValue)}/share`} strong />
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Key assumptions">
          <table className="data-table" style={{ width: "100%" }}>
            <tbody>
              <Line label="Discount rate (WACC)" value={pct(r.wacc, 2)} />
              <Line label="Long-run growth after year 10" value={pct(r.terminalGrowth, 2)} />
              <Line label="Share of value from after year 10" value={pct(r.terminalShare)} />
              <Line
                label="Beta (ours · broker's)"
                value={`${r.beta === null ? DASH : r.beta.toFixed(2)}${r.betaBroker !== null ? ` · broker ${r.betaBroker.toFixed(2)}` : ""}`}
              />
              {rule ? <Line label={`Risk-free rate${rule.riskFreeSource ? ` (${rule.riskFreeSource})` : ""}`} value={pct(rule.riskFree, 2)} /> : null}
              {rule ? <Line label="Equity risk premium" value={pct(rule.equityPremium, 1)} /> : null}
              <Line label="EV / EBITDA" value={r.impliedEvEbitda === null ? DASH : `${r.impliedEvEbitda.toFixed(1)}×`} />
              <Line label="Market cap" value={big(r.marketCap)} />
              <Line label="Price as of" value={r.spotDate ?? DASH} />
            </tbody>
          </table>
        </Card>
      </div>

      {r.flags.length > 0 && (
        <Card title="Notes">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {r.flags.map((f) => (
              <li key={f}>{flagText(f)}</li>
            ))}
          </ul>
        </Card>
      )}

      {r.model !== null && <Advanced r={r} m={r.model} />}
    </div>
  );
}

// ----------------------------------------------------------------------------------------- advanced

/** Where a line of the base year came from, in words: a filing tag, a derivation, or Dolt. */
export function sourceText(src: string | undefined, filed: string | null): string {
  if (src === undefined) return "not stated";
  if (src.startsWith("Dolt")) return src === "Dolt" ? "Dolt annual statements" : "Dolt annual statements (the filing does not state it)";
  if (src.startsWith("derived")) return `${src.replace(/^derived: /, "derived from the filing: ")}`;
  return `SEC filing${filed ? ` of ${filed}` : ""}: ${src}`;
}

function Row3({ label, value, source }: { label: string; value: string; source: string }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num" style={{ textAlign: "right", fontFamily: "var(--num-face)" }}>
        {value}
      </td>
      <td className="muted" style={{ fontSize: 12 }}>
        {source}
      </td>
    </tr>
  );
}

const TH = { textAlign: "left" as const, fontWeight: 600 };

/** The model's whole working: every input with its source, the discount-rate build, the year-by-year
 *  forecast to the stable year, the share count and the history the ratios are medians of. */
function Advanced({ r, m }: { r: ValuationRow; m: ValuationModel }) {
  const c = m.capital;
  const d = m.drivers;
  const b = r.bridge;
  const base = m.history[m.history.length - 1];
  const filed = m.data.filed;
  const s = m.sources;
  const cons = m.consensus.years;
  const consText = (i: number) => {
    const y = cons[i];
    return y
      ? `Analyst consensus for the year to ${y.periodEnd} (${y.analysts ?? "?"} analysts, range ${big(y.low)}–${big(y.high)}), snapshot ${m.consensus.snapshot ?? DASH}`
      : "no consensus: the line fades from the history";
  };
  const f = r.forecast ?? [];
  return (
    <details>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Advanced: every input and where it came from</summary>
      <div style={{ display: "grid", gap: 10, marginTop: 8 }}>
        <Card title="Every input and where it came from">
          <table className="data-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th style={TH}>Input</th>
                <th className="num">Value</th>
                <th style={TH}>Source</th>
              </tr>
            </thead>
            <tbody>
              <Row3 label={`Revenue, fiscal year to ${m.data.fiscalYearEnd ?? DASH}`} value={big(base?.revenue ?? null)} source={sourceText(s["revenue"], filed)} />
              <Row3 label="Operating income (EBIT)" value={pct(base?.ebitMargin ?? null) + " of revenue"} source={sourceText(s["ebit"], filed)} />
              <Row3 label="Depreciation and amortisation" value={pct(base?.daPct ?? null) + " of revenue"} source={sourceText(s["da"], filed)} />
              <Row3 label="Capital spending" value={pct(base?.capexPct ?? null) + " of revenue"} source={sourceText(s["capex"], filed)} />
              <Row3 label="Interest expense" value={c.coverage === null ? "none" : `coverage ${c.coverage.toFixed(1)}×`} source={sourceText(s["interest"], filed)} />
              <Row3 label="Cash and marketable securities" value={b ? big(b.cash) : DASH} source={sourceText(s["cash"], filed)} />
              <Row3 label="Debt" value={b ? big(b.debt) : DASH} source={sourceText(s["debt"], filed)} />
              <Row3 label="Basic shares" value={b ? count(b.basicShares) : DASH} source={sourceText(s["shares"], filed)} />
              <Row3 label="Year 1 revenue growth" value={signedPct(f[0]?.growth ?? null)} source={consText(0)} />
              <Row3 label="Year 2 revenue growth" value={signedPct(f[1]?.growth ?? null)} source={consText(1)} />
              <Row3 label="Years 3–10 revenue growth" value={`fades to ${pct(r.terminalGrowth, 1)}`} source="A straight line from the last consensus year to the long-run rate in year 10" />
              <Row3 label="Long-run operating margin" value={pct(d.ebitMargin)} source={`Median of the last ${d.historyYears ?? 5} fiscal years`} />
              <Row3 label="Long-run D&A" value={pct(d.daPct) + " of revenue"} source={`Median of the last ${d.historyYears ?? 5} fiscal years`} />
              <Row3 label="Long-run capital spending" value={pct(d.capexPct) + " of revenue"} source={`Median of the last ${d.historyYears ?? 5} fiscal years`} />
              <Row3
                label="Working capital"
                value={pct(d.wcPct) + " of revenue"}
                source={d.wcIsLevel ? "Median level of the last five years, from the balance sheet (floored at zero)" : "Median yearly investment of the last five years (no balance-sheet level)"}
              />
              <Row3 label="Tax rate" value={pct(d.taxRate)} source="Median effective rate of the last five years, loss years excluded, bounded 10–35%" />
              <Row3 label="Growth after year 10" value={pct(r.terminalGrowth, 2)} source="Declared long-run rate (below the 10-year Treasury)" />
              <Row3 label="Return on capital after year 10" value={pct(m.terminal.roic, 2)} source="Equal to the discount rate (stable growth, Damodaran): growth adds no value after year 10" />
              <Row3 label="Risk-free rate" value={pct(c.rf, 2)} source={c.rfSource ?? DASH} />
              <Row3 label="Equity risk premium" value={pct(c.erp, 2)} source={c.erpMonth ? `Damodaran implied ERP, ${c.erpMonth}` : "fallback (no Damodaran file)"} />
              <Row3 label="Beta" value={c.beta === null ? DASH : c.beta.toFixed(2)} source={`5 years of weekly total returns vs SPY, raw ${c.betaRaw?.toFixed(2) ?? DASH}, Blume-adjusted (0.67 × raw + 0.33)`} />
              <Row3
                label="Pre-tax cost of debt"
                value={pct(c.kdPreTax, 2)}
                source={`Risk-free + ${pct(c.spread, 2)} for a ${c.rating ?? DASH} synthetic rating from interest coverage (Damodaran's table, ${c.ratingsAsOf ?? DASH})`}
              />
              <Row3 label="Tax on interest" value={pct(c.marginalTax, 0)} source="US marginal rate" />
            </tbody>
          </table>
        </Card>

        <Card title="Discount rate (WACC) build">
          <table className="data-table" style={{ width: "100%" }}>
            <tbody>
              <Row3 label="Cost of equity" value={pct(c.ke, 2)} source={`${pct(c.rf, 2)} + ${c.beta?.toFixed(2) ?? DASH} × ${pct(c.erp, 2)}`} />
              <Row3 label="After-tax cost of debt" value={pct(c.kdAfterTax, 2)} source={`${pct(c.kdPreTax, 2)} × (1 − ${pct(c.marginalTax, 0)})`} />
              <Row3 label="Equity weight" value={pct(c.equityWeight)} source="Market value: price × basic shares" />
              <Row3 label="Debt weight" value={pct(c.debtWeight)} source="Book debt and finance leases" />
              <Row3 label="WACC" value={pct(c.wacc, 2)} source="Equity weight × cost of equity + debt weight × after-tax cost of debt" />
            </tbody>
          </table>
        </Card>

        <Card title="Year-by-year calculation">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={TH}>Line</th>
                  {f.map((y) => (
                    <th key={y.year} className="num">
                      Y{y.year}
                    </th>
                  ))}
                  <th className="num">Y11 (stable)</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Revenue source</td>
                  {f.map((y) => (
                    <td key={y.year} className="muted" style={{ fontSize: 11 }}>
                      {y.growthSource ?? DASH}
                    </td>
                  ))}
                  <td />
                </tr>
                {(
                  [
                    ["Revenue", (y) => big(y.revenue)],
                    ["Growth", (y) => signedPct(y.growth)],
                    ["EBIT", (y) => big(y.ebit)],
                    ["NOPAT", (y) => big(y.nopat)],
                    ["+ D&A", (y) => big(y.da)],
                    ["− Capital spending", (y) => big(y.capex)],
                    ["− Working capital", (y) => big(y.wcChange)],
                    ["= Free cash flow", (y) => big(y.fcf)],
                    ["Discount (mid-year)", (y) => (y.discount === null ? DASH : y.discount.toFixed(4))],
                    ["Present value", (y) => big(y.pv)],
                  ] as [string, (y: (typeof f)[number]) => string][]
                ).map(([label, fn]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    {f.map((y) => (
                      <td key={y.year} className="num" style={{ fontFamily: "var(--num-face)" }}>
                        {fn(y)}
                      </td>
                    ))}
                    <td className="num" style={{ fontFamily: "var(--num-face)" }}>
                      {label === "= Free cash flow" ? big(m.terminal.fcf) : label === "Growth" ? pct(r.terminalGrowth, 1) : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Year 11 reinvests {pct(m.terminal.reinvestmentRate)} of its after-tax operating profit to grow at {pct(r.terminalGrowth, 1)};
            the value after year 10 is {big(m.terminal.value)}, {big(b?.pvTerminal ?? null)} today.
          </div>
        </Card>

        {b !== null && (
          <Card title="Share count and dilution">
            <table className="data-table" style={{ width: "100%" }}>
              <tbody>
                <Row3 label="Basic shares outstanding" value={count(b.basicShares)} source={sourceText(s["shares"], filed)} />
                <Row3 label="Unvested restricted units" value={count(b.rsu)} source="Counted at 100% vesting" />
                <Row3 label="Options (treasury-stock method)" value={count(b.optionShares)} source="At the modelled value per share, not today's price" />
                <Row3 label="Shares the value is divided by" value={count(b.shares)} source="" />
              </tbody>
            </table>
          </Card>
        )}

        <Card title="Company history">
          <table className="data-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th style={TH}>Fiscal year</th>
                <th className="num">Revenue</th>
                <th className="num">EBIT margin</th>
                <th className="num">D&A</th>
                <th className="num">Capex</th>
                <th className="num">Working capital</th>
                <th className="num">Tax</th>
              </tr>
            </thead>
            <tbody>
              {m.history.map((y) => (
                <tr key={y.end}>
                  <td>{y.end}</td>
                  <td className="num">{big(y.revenue)}</td>
                  <td className="num">{pct(y.ebitMargin)}</td>
                  <td className="num">{pct(y.daPct)}</td>
                  <td className="num">{pct(y.capexPct)}</td>
                  <td className="num">{pct(y.wcPct)}</td>
                  <td className="num">{pct(y.taxRate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            {m.data.source ?? DASH}
            {m.data.sic ? ` · SIC ${m.data.sic} (${m.data.sicDescription ?? ""})` : ""}
            {m.data.latestPeriod ? ` · latest results for the period to ${m.data.latestPeriod}, filed ${m.data.latestFiled ?? DASH}` : ""}
          </div>
          {Object.keys(m.disagree).length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
              {Object.entries(m.disagree).map(([line, [filing, dolt]]) => (
                <li key={line}>
                  {line}: the filing states {big(filing)}, Dolt {big(dolt)}; the value uses the filing.
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </details>
  );
}
