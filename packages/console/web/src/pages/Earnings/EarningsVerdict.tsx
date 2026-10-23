import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import type { VerdictArm, VerdictResult } from "@console/shared";
import { AXIS_FONT, CHART_GRID, CHART_GRID_STRONG } from "../../components/Charts";
import { DataCard, PnlCell } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney, fmtPct } from "../../lib/format";

/**
 * earnings' strategies page (2026-10-23): the verdict board. One question per arm -- is there an
 * edge after costs, and how sure are we -- drawn from the module's own derivation
 * (`strategy_report --json`, `cherrypick.earnings.verdict`); nothing here computes a figure.
 *
 * It replaced three tabs that disagreed with each other: `strategies` pooled the advised twins
 * into their controls, labelled any arm with 30 trades "significant", and repeated its own
 * comparison table twice; `performance` overlapped its break labels; `analysis` was generic. The
 * interval resamples earnings EVENTS, because every strategy opens on the same name the same
 * night -- ten trades on one report are one draw. `?arm=` opens one arm's drill-down.
 */

export function useVerdict(era: string | null, flagged: boolean) {
  return useQuery<VerdictResult>({
    queryKey: ["earnings-verdict", era, flagged],
    queryFn: async () => {
      const q = new URLSearchParams();
      if (era !== null) q.set("era", era);
      if (flagged) q.set("flagged", "1");
      const res = await fetch(`/api/earnings/verdict?${q.toString()}`);
      if (!res.ok) throw new Error(`earnings verdict: HTTP ${res.status}`);
      return (await res.json()) as VerdictResult;
    },
    refetchInterval: 120_000,
  });
}

const pctFmt = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

/** Where an interval sits against zero -- a statistical fact, stated, never a tone. */
function intervalRead(a: VerdictArm): string {
  const ci = a.ci.ror_pct;
  if (ci === null) return "too few events for an interval";
  if (ci[0] > 0) return "interval above zero";
  if (ci[1] < 0) return "interval below zero";
  return "interval spans zero";
}

function armLabel(a: VerdictArm): string {
  return a.strategy.replaceAll("_", " ");
}

/**
 * The edge chart: net ÷ capital at risk per arm, a dot at the point estimate and a line across its
 * 90% event-clustered interval, against one zero. One axis, so a $300 butterfly and a $3,000 iron
 * fly read on the same scale. A retired arm is drawn faint and says when it stopped.
 */
function EdgeChart({ arms, armHref }: { arms: VerdictArm[]; armHref: (arm: string) => string }) {
  const width = 1150;
  const rowH = 30;
  const m = { l: 250, r: 150, t: 22, b: 8 };
  const height = m.t + m.b + rowH * arms.length;
  const vals = arms.flatMap((a) => [a.ror_pct ?? 0, ...(a.ci.ror_pct ?? [])]);
  const span = Math.max(10, ...vals.map((v) => Math.abs(v)));
  const lo = -span * 1.05;
  const hi = span * 1.05;
  const X = (v: number) => m.l + ((v - lo) / (hi - lo)) * (width - m.l - m.r);
  const step = span > 60 ? 50 : span > 30 ? 20 : 10;
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="net as a share of capital at risk, by arm, with 90% intervals"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      {ticks.map((v) => (
        <g key={v}>
          <line
            x1={X(v)}
            y1={m.t - 6}
            x2={X(v)}
            y2={height - m.b}
            stroke={v === 0 ? CHART_GRID_STRONG : CHART_GRID}
            strokeWidth={v === 0 ? 1.5 : 1}
          />
          <text x={X(v)} y={m.t - 10} textAnchor="middle" {...AXIS_FONT}>
            {v === 0 ? "0" : pctFmt(v)}
          </text>
        </g>
      ))}
      {arms.map((a, i) => {
        const y = m.t + rowH * i + rowH / 2;
        const point = a.ror_pct;
        const ci = a.ci.ror_pct;
        const color = point === null ? "var(--text-muted)" : point >= 0 ? "var(--pos)" : "var(--neg)";
        const faint = a.retired !== null;
        return (
          <Link key={a.arm} to={armHref(a.arm)} aria-label={`${armLabel(a)}: open the drill-down`}>
            <g opacity={faint ? 0.45 : 1} style={{ cursor: "pointer" }}>
              <title>
                {`${armLabel(a)} · ${a.n} trades over ${a.ci.events} events\n` +
                  `net ${fmtMoney(a.net)} on ${fmtMoney(a.capital_at_risk)} at risk = ${point === null ? "—" : pctFmt(point)}\n` +
                  (ci === null ? "no interval" : `90% interval ${pctFmt(ci[0])} to ${pctFmt(ci[1])} · ${intervalRead(a)}`)}
              </title>
              {/* the whole row is the hit target, not just the dot */}
              <rect x={0} y={y - rowH / 2} width={width} height={rowH} fill="transparent" />
              <text x={8} y={y + 4} fontSize={13} fill="var(--text)">
                {armLabel(a)}
              </text>
              <text x={m.l - 12} y={y + 4} textAnchor="end" {...AXIS_FONT} fontSize={11}>
                {faint ? `retired ${a.retired}` : `n ${a.n}`}
              </text>
              {ci !== null && <line x1={X(ci[0])} y1={y} x2={X(ci[1])} y2={y} stroke={color} strokeWidth={2} strokeLinecap="round" />}
              {point !== null && <circle cx={X(point)} cy={y} r={5} fill={color} stroke="var(--card)" strokeWidth={2} />}
              <text x={width - m.r + 12} y={y + 4} {...AXIS_FONT} fontSize={11}>
                {point === null ? "—" : pctFmt(point)}
              </text>
            </g>
          </Link>
        );
      })}
    </svg>
  );
}

/** Cumulative net by trade number, with each measurement break as a faint tick and no label on the
 *  plot -- the names live in the hover and the integrity strip, where they cannot overlap. */
function CumulativeMini({ arm, breaks }: { arm: VerdictArm; breaks: Array<{ date: string; key: string }> }) {
  const pts = arm.cumulative;
  if (pts.length < 2) return <p className="muted">fewer than two closed trades</p>;
  const width = 360;
  const height = 120;
  const m = { l: 4, r: 4, t: 6, b: 6 };
  const ys = pts.map((p) => p.cumulative);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const X = (i: number) => m.l + ((i - 1) / (pts.length - 1)) * (width - m.l - m.r);
  const Y = (v: number) => m.t + ((hi - v) / (hi - lo || 1)) * (height - m.t - m.b);
  const ticks = breaks
    .map((b) => ({ ...b, i: pts.find((p) => (p.opened ?? "") >= b.date)?.i }))
    .filter((b): b is { date: string; key: string; i: number } => b.i !== undefined && b.i > 1);
  const last = ys[ys.length - 1]!;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`cumulative net of ${armLabel(arm)} by trade`}
      // A fixed height, stretched to the card's width: a card with a long exit-reason list must not
      // shrink its line to a sliver. Strokes stay their own width under the stretch.
      preserveAspectRatio="none"
      style={{ width: "100%", height: 96, flexShrink: 0, display: "block" }}
    >
      <line x1={m.l} y1={Y(0)} x2={width - m.r} y2={Y(0)} stroke={CHART_GRID_STRONG} vectorEffect="non-scaling-stroke" />
      {ticks.map((b) => (
        <line key={`${b.key}-${b.date}`} x1={X(b.i)} y1={m.t} x2={X(b.i)} y2={height - m.b} stroke={CHART_GRID} strokeDasharray="2 3" vectorEffect="non-scaling-stroke">
          <title>{`${b.date} · ${b.key}`}</title>
        </line>
      ))}
      <polyline
        points={pts.map((p) => `${X(p.i).toFixed(1)},${Y(p.cumulative).toFixed(1)}`).join(" ")}
        fill="none"
        stroke={last >= 0 ? "var(--pos)" : "var(--neg)"}
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function ArmCard({ arm, breaks, href }: { arm: VerdictArm; breaks: Array<{ date: string; key: string }>; href: string }) {
  return (
    // The label is the link rather than `to`: GridCard appends the current query string to `to`,
    // and the drill-down is itself a query (`?arm=`), so `to` would write two `?`s.
    <GridCard
      label={
        <Link to={href} className="gcard-label-link" title={`${armLabel(arm)}: trades, stops and covariates`}>
          {armLabel(arm)}
          {arm.retired !== null ? ` · retired ${arm.retired}` : ""}
        </Link>
      }
      span={4}
      h={304}
      foot={`${arm.n} trades · net ${fmtMoney(arm.net)} · without its worst 3 events ${fmtMoney(arm.concentration.net_ex_worst3)}`}
    >
      <CumulativeMini arm={arm} breaks={breaks} />
      <div className="card-subhead">net by exit reason</div>
      <DivergingBars
        rows={arm.by_exit_reason.map((r) => ({
          label: `${r.reason.replaceAll("_", " ")} · ${r.n}`,
          value: r.net,
        }))}
        format={fmtMoney}
      />
    </GridCard>
  );
}

function NotReady({ q }: { q: ReturnType<typeof useVerdict> }) {
  if (q.isLoading) return <p className="muted">reading the verdict…</p>;
  if (q.isError) return <p className="muted">the verdict could not be read</p>;
  return <p className="muted">{q.data?.error ?? "no verdict"}</p>;
}

export function EarningsVerdict({ era }: { era: string | null }) {
  const [params, setParams] = useSearchParams();
  const flagged = params.get("flagged") === "1";
  const armParam = params.get("arm");
  const q = useVerdict(era, flagged);
  const v = q.data?.verdict ?? null;

  const hrefFor = (arm: string | null) => {
    const next = new URLSearchParams(params);
    if (arm === null) next.delete("arm");
    else next.set("arm", arm);
    const s = next.toString();
    return `/earnings/strategies${s === "" ? "" : `?${s}`}`;
  };
  const toggleFlagged = () => {
    const next = new URLSearchParams(params);
    if (flagged) next.delete("flagged");
    else next.set("flagged", "1");
    setParams(next, { replace: true });
  };

  if (v === null) return <NotReady q={q} />;
  const excludedN = v.excluded.reduce((s, e) => s + e.n, 0);
  const scopeLine = (
    <div
      className="muted"
      style={{
        fontSize: 12,
        display: "flex",
        gap: "0.75rem",
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <span>
        controls only · net = gross − every cost · {Math.round(v.ci_level * 100)}% intervals resample earnings events (seed{" "}
        {v.bootstrap.seed})
      </span>
      <button
        type="button"
        className={`chip ${flagged ? "scope-select-off-default" : ""}`}
        onClick={toggleFlagged}
        title={v.flag_rules.map((r) => `${r.key}: ${r.note}`).join("\n")}
      >
        {flagged ? "including flagged rows" : `${excludedN} flagged rows left out`}
      </button>
    </div>
  );

  const picked = armParam === null ? null : (v.arms.find((a) => a.arm === armParam) ?? null);
  if (armParam !== null) {
    return picked === null ? (
      <p className="muted">
        no closed trades for {armParam} in this scope · <Link to={hrefFor(null)}>every arm</Link>
      </p>
    ) : (
      <ArmDrill arm={picked} twins={v.twins.filter((t) => t.strategy === picked.strategy)} back={hrefFor(null)} scopeLine={scopeLine} />
    );
  }

  const live = v.arms.filter((a) => a.retired === null);
  return (
    <div className="grid-12">
      <div className="span-12">{scopeLine}</div>
      <GridCard
        label="edge after costs — net ÷ capital at risk, 90% interval"
        span={12}
        h={304}
        foot="a dot right of zero with its whole line right of zero is an edge this sample can show; a line across zero is not yet a read either way"
      >
        <EdgeChart arms={v.arms} armHref={hrefFor} />
      </GridCard>

      <div className="span-12">
        <DataCard
          title="Where the money goes (net of costs)"
          headers={[
            "arm",
            "trades",
            "win %",
            "gross",
            "costs",
            "net",
            "cost drag",
            "net ÷ risk",
            "90% interval",
            "without worst 3 events",
            "stops · green first",
          ]}
          numFrom={1}
          loading={false}
          rowCount={v.arms.length}
        >
          {v.arms.map((a) => (
            <tr key={a.arm} className={a.retired !== null ? "muted" : ""}>
              <td>
                <Link to={hrefFor(a.arm)}>{armLabel(a)}</Link>
              </td>
              <td>{a.n}</td>
              <td>{fmtPct(a.win_pct)}</td>
              <td>
                <PnlCell v={a.gross} />
              </td>
              <td>{fmtMoney(-a.costs)}</td>
              <td>
                <PnlCell v={a.net} />
              </td>
              <td title="costs ÷ premium at entry">{fmtPct(a.cost_drag_pct, 1)}</td>
              <td>{a.ror_pct === null ? "—" : pctFmt(a.ror_pct)}</td>
              <td className="muted">{a.ci.ror_pct === null ? "—" : `${pctFmt(a.ci.ror_pct[0])} to ${pctFmt(a.ci.ror_pct[1])}`}</td>
              <td>
                <PnlCell v={a.concentration.net_ex_worst3} />
              </td>
              <td title="stop exits, and how many were in the green first">
                {a.stops.n} · {a.stops.green_first}
              </td>
            </tr>
          ))}
        </DataCard>
      </div>

      {/* arm cards: direct children of the board's grid */}
      {[...live, ...v.arms.filter((a) => a.retired !== null)].map((a) => (
        <ArmCard key={a.arm} arm={a} breaks={v.breaks} href={hrefFor(a.arm)} />
      ))}

      {v.twins.length > 0 && (
        <div className="span-12">
          <TwinTable twins={v.twins} />
        </div>
      )}
    </div>
  );
}

function TwinTable({ twins }: { twins: NonNullable<VerdictResult["verdict"]>["twins"] }) {
  return (
    <DataCard
      title="Advised twins against their control (matched symbol, strategy, session)"
      headers={["twin", "pairs", "twin net", "control net", "twin − control", "twin better"]}
      numFrom={1}
      loading={false}
      rowCount={twins.length}
    >
      {twins.map((t) => (
        <tr key={t.arm}>
          <td>{t.arm}</td>
          <td>{t.pairs}</td>
          <td>
            <PnlCell v={t.twin_net} />
          </td>
          <td>
            <PnlCell v={t.control_net} />
          </td>
          <td>
            <PnlCell v={t.diff} />
          </td>
          <td>
            {t.twin_better} of {t.pairs}
          </td>
        </tr>
      ))}
    </DataCard>
  );
}

/** Trade nets in fixed-width bins, a bar per bin coloured by the bin's side of zero. */
function NetHistogram({ arm }: { arm: VerdictArm }) {
  const nets = arm.trades.map((t) => t.net);
  if (nets.length === 0) return <p className="muted">no trades</p>;
  const lo = Math.min(0, ...nets);
  const hi = Math.max(0, ...nets);
  const bins = 16;
  const w = (hi - lo || 1) / bins;
  const counts = Array.from({ length: bins }, () => 0);
  for (const n of nets) counts[Math.min(bins - 1, Math.floor((n - lo) / w))]! += 1;
  const width = 1150;
  const height = 200;
  const m = { l: 8, r: 8, t: 10, b: 22 };
  const bw = (width - m.l - m.r) / bins;
  const maxC = Math.max(...counts);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="trades by net result"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      {counts.map((c, i) => {
        const from = lo + i * w;
        const h = maxC === 0 ? 0 : (c / maxC) * (height - m.t - m.b);
        const mid = from + w / 2;
        return (
          <g key={i}>
            <title>{`${fmtMoney(from)} to ${fmtMoney(from + w)}: ${c} trade${c === 1 ? "" : "s"}`}</title>
            <rect x={m.l + i * bw} y={m.t} width={bw} height={height - m.t - m.b} fill="transparent" />
            {c > 0 && (
              <rect
                x={m.l + i * bw + 1}
                y={height - m.b - h}
                width={bw - 2}
                height={h}
                rx={4}
                fill={mid >= 0 ? "var(--pos)" : "var(--neg)"}
              />
            )}
          </g>
        );
      })}
      <line x1={m.l} y1={height - m.b} x2={width - m.r} y2={height - m.b} stroke={CHART_GRID_STRONG} />
      <text x={m.l} y={height - 6} {...AXIS_FONT}>
        {fmtMoney(lo)}
      </text>
      <text x={width - m.r} y={height - 6} textAnchor="end" {...AXIS_FONT}>
        {fmtMoney(hi)}
      </text>
    </svg>
  );
}

const COVARIATE_LABEL: Record<string, string> = {
  term_structure: "term structure",
  iv_rv_ratio: "IV / RV",
  winrate: "historical winrate",
  expected_move_pct: "expected move",
  implied_vs_avg_actual: "implied ÷ average actual move",
};

function ArmDrill({
  arm,
  twins,
  back,
  scopeLine,
}: {
  arm: VerdictArm;
  twins: NonNullable<VerdictResult["verdict"]>["twins"];
  back: string;
  scopeLine: React.ReactNode;
}) {
  const ci = arm.ci.ror_pct;
  const worstFirst = [...arm.trades].sort((a, b) => a.net - b.net);
  return (
    <div className="grid-12">
      <div
        className="span-12"
        style={{
          display: "flex",
          gap: "1rem",
          alignItems: "baseline",
          flexWrap: "wrap",
        }}
      >
        <Link to={back}>← every arm</Link>
        <h2 style={{ margin: 0 }}>{armLabel(arm)}</h2>
        {arm.retired !== null && <span className="muted">retired {arm.retired}</span>}
      </div>
      <div className="span-12">{scopeLine}</div>
      {/* the four tiles sit on the page grid */}
      <StatTile
        label="net ÷ capital at risk"
        value={arm.ror_pct === null ? null : pctFmt(arm.ror_pct)}
        tone={(arm.ror_pct ?? 0) >= 0 ? "pos" : "neg"}
        foot={ci === null ? "too few events for an interval" : `90% interval ${pctFmt(ci[0])} to ${pctFmt(ci[1])}`}
      />
      <StatTile
        label="net"
        value={fmtMoney(arm.net)}
        tone={arm.net >= 0 ? "pos" : "neg"}
        foot={`${arm.n} trades · ${arm.ci.events} events · expectancy ${fmtMoney(arm.expectancy)}`}
      />
      <StatTile
        label="costs"
        value={fmtMoney(-arm.costs)}
        tone="dim"
        foot={`gross ${fmtMoney(arm.gross)} · cost drag ${fmtPct(arm.cost_drag_pct, 1)} of premium`}
      />
      <StatTile
        label="stop exits"
        value={String(arm.stops.n)}
        tone="dim"
        foot={`${fmtMoney(arm.stops.net)} · ${arm.stops.green_first} were in the green first (${fmtMoney(arm.stops.green_first_net)})`}
      />

      <GridCard label="trades by net result" span={12} h={248} foot="each bar a fixed-width band of net; hover for its range and count">
        <NetHistogram arm={arm} />
      </GridCard>

      <div className="span-12">
        <DataCard
          title="Net ÷ capital at risk by tercile of each entry reading"
          headers={["reading", "trades", "low third", "middle third", "high third"]}
          numFrom={1}
          loading={false}
          rowCount={Object.keys(arm.covariates).length}
        >
          {Object.entries(arm.covariates).map(([k, c]) => (
            <tr key={k}>
              <td>{COVARIATE_LABEL[k] ?? k}</td>
              <td>{c.n}</td>
              {c.buckets.length === 0 ? (
                <td colSpan={3} className="muted">
                  too few readings to split
                </td>
              ) : (
                c.buckets.map((b, i) => (
                  <td key={i} title={`${b.lo} to ${b.hi} · ${b.n} trades · net ${fmtMoney(b.net)}`}>
                    <span className={(b.ror_pct ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>{b.ror_pct === null ? "—" : pctFmt(b.ror_pct)}</span>
                    <span className="muted">
                      {" "}
                      ({b.lo.toFixed(2)}–{b.hi.toFixed(2)})
                    </span>
                  </td>
                ))
              )}
            </tr>
          ))}
        </DataCard>
      </div>
      <p className="muted span-12" style={{ fontSize: 12, margin: 0 }}>
        Terciles are a read across readings, not a test: five readings over a few dozen events will show a spread by chance. Declare a
        threshold before trusting one.
      </p>

      {twins.length > 0 && (
        <div className="span-12">
          <TwinTable twins={twins} />
        </div>
      )}

      <div className="span-12">
        <DataCard
          title={`Trades, worst first · worst three events ${arm.concentration.worst_events.map((e) => `${e.symbol} ${fmtMoney(e.net)}`).join(" · ")}`}
          headers={["opened", "sym", "exit", "net", "at risk", "best mark"]}
          numFrom={3}
          loading={false}
          rowCount={worstFirst.length}
        >
          {worstFirst.map((t) => (
            <tr key={t.order_id}>
              <td>{t.opened ?? "—"}</td>
              <td>{t.symbol}</td>
              <td className="muted">{(t.exit_reason ?? "n/r").replaceAll("_", " ")}</td>
              <td>
                <PnlCell v={t.net} />
              </td>
              <td>{fmtMoney(t.capital_at_risk)}</td>
              <td>{t.max_unrealized_pnl === null ? "—" : <PnlCell v={t.max_unrealized_pnl} />}</td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
