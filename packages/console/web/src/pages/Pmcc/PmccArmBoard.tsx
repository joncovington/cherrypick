import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import type { PmccBridged, PmccOpenPosition, PmccTracker } from "@console/shared";
import { usePmcc, usePmccTracker } from "../../lib/api";
import { fmtCash, fmtMoney } from "../../lib/format";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";

/**
 * PMCC by arm (2026-11-04, the user's pick "B" off the mock-up canvas): a column per arm, the symbols
 * it holds stacked in it. PMCC is read per symbol, never pooled (packages/pmcc CLAUDE.md), so an arm's
 * head shows a bar per symbol it holds, never a total across them.
 *
 * A symbol card opens the shared lightbox on that symbol (`?pos=` names the position it was opened
 * from): a tab per open position on the symbol, each its P&L since entry and its chain (the long
 * bought, every short sold and how it ended), and an "all arms" tab with every position's P&L on one
 * chart, the same market path. Every figure is the module's: the open positions from `/api/pmcc`, the
 * chain and the P&L from the tracker bridged from `analytics` (valued at each week's close, the last
 * point now). Nothing new is read.
 */

/** Each arm's rule in a line, from the module's design table. */
export const PMCC_ARM_RULE: Record<string, string> = {
  shield: "held ~1-year long, weekly short; also rolls early on decay or a breach",
  shield_hold: "held ~1-year long, weekly 0.70Δ short rolled on its expiry day; the 30% stop",
  shield_guard: "as shield_hold, and rolls an in-the-money short once it is assignment-exposed",
  control: "retired 2026-10-06: a ~21-DTE long re-bought every cycle",
};

const ARM_ORDER = ["shield", "shield_hold", "shield_guard"];
const LINE_COLOURS = ["var(--accent)", "var(--chart-special)", "var(--amber-line)", "var(--live)", "var(--chart-orange)"];

export interface ArmColumn {
  arm: string;
  positions: PmccOpenPosition[];
  exposed: number;
}

/** Open positions grouped by arm, the module's arms first; within an arm, by symbol. */
export function armColumns(open: PmccOpenPosition[]): ArmColumn[] {
  const arms = [...new Set(open.map((p) => p.arm))].sort((a, b) => {
    const ia = ARM_ORDER.indexOf(a);
    const ib = ARM_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return arms.map((arm) => {
    const positions = open.filter((p) => p.arm === arm).sort((a, b) => a.symbol.localeCompare(b.symbol));
    return { arm, positions, exposed: positions.filter((p) => p.exposedTicks > 0).length };
  });
}

function legText(strike: number | null, exp: string | null): string {
  return strike === null ? "—" : `${exp ?? "?"} ${strike}C`;
}

function tone(v: number | null): string {
  return v === null ? "muted" : v < 0 ? "pnl-neg" : v > 0 ? "pnl-pos" : "";
}

/** One bar per symbol: each symbol's net, never summed. */
function SymbolBars({ positions }: { positions: PmccOpenPosition[] }) {
  const max = Math.max(1, ...positions.map((p) => Math.abs(p.unrealisedNet ?? 0)));
  const W = 200;
  const H = 56;
  const w = W / Math.max(1, positions.length);
  return (
    <div className="pmcc-bars">
      <svg viewBox={`0 0 ${String(W)} ${String(H)}`} preserveAspectRatio="none" role="img" aria-label="net on each symbol the arm holds">
        <line x1={0} x2={W} y1={H / 2} y2={H / 2} className="study-spark-zero" vectorEffect="non-scaling-stroke" />
        {positions.map((p, i) => {
          const v = p.unrealisedNet;
          const h = v === null ? 0 : (Math.abs(v) / max) * (H / 2 - 2);
          return (
            <rect
              key={p.positionId}
              x={i * w + w * 0.18}
              width={w * 0.64}
              y={v !== null && v >= 0 ? H / 2 - h : H / 2}
              height={Math.max(h, v === null ? 0 : 1)}
              rx={2}
              className={v === null ? "pmcc-bar-nr" : v < 0 ? "pmcc-bar-neg" : "pmcc-bar-pos"}
            />
          );
        })}
      </svg>
      <div className="pmcc-bar-labels">
        {positions.map((p) => (
          <span key={p.positionId}>{p.symbol}</span>
        ))}
      </div>
    </div>
  );
}

function SymbolCard({ p, onOpen }: { p: PmccOpenPosition; onOpen: () => void }) {
  const v = p.unrealisedNet;
  return (
    <button type="button" className={v !== null && v < 0 ? "pmcc-sym is-neg" : "pmcc-sym"} onClick={onOpen} aria-label={`${p.symbol} ${p.arm}: net ${fmtMoney(v)}`}>
      <span className="pmcc-sym-top">
        <span className="pmcc-sym-name">{p.symbol}</span>
        <span className={`pmcc-sym-net ${tone(v)}`}>{fmtMoney(v)}</span>
      </span>
      <span className="pmcc-sym-legs">
        long {legText(p.longStrike, p.longExpiration)} · short {legText(p.shortStrike, p.shortExpiration)}
      </span>
      <span className="pmcc-sym-foot">
        <span>
          {p.rollCount ?? 0} roll{p.rollCount === 1 ? "" : "s"} · since {p.entrySession}
        </span>
        {p.exposedTicks > 0 ? (
          <span className="chip chip-warn" title={`the short's extrinsic was under the assignment threshold on ${p.exposedTicks} of ${p.markedTicks} marked ticks`}>
            exposed {p.exposedTicks}
          </span>
        ) : null}
      </span>
    </button>
  );
}

interface PnlLine {
  key: string;
  label: string;
  colour: string;
  points: Array<{ date: string; net: number }>;
}

/** The tracker's P&L since entry: zero at the entry session, then each week's close (the last now). */
export function pnlPoints(t: PmccTracker): Array<{ date: string; net: number }> {
  const pts = [{ date: t.position.entrySession, net: 0 }];
  for (const w of t.weeks) if (w.priced && w.net !== null) pts.push({ date: w.weekEnd, net: w.net });
  return pts;
}

const day = (d: string) => Date.parse(`${d.slice(0, 10)}T00:00:00Z`) / 86_400_000;

function PnlChart({ lines, marks = [] }: { lines: PnlLine[]; marks?: Array<{ date: string; label: string }> }) {
  const drawn = lines.filter((l) => l.points.length > 1);
  if (drawn.length === 0) return <p className="muted">No priced week yet to draw.</p>;
  const W = 640;
  const H = 200;
  const pad = { l: 56, r: 10, t: 14, b: 22 };
  const xs = drawn.flatMap((l) => l.points.map((p) => day(p.date)));
  const ys = drawn.flatMap((l) => l.points.map((p) => p.net));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs, x0 + 1);
  const top = Math.max(0, ...ys);
  const bottom = Math.min(0, ...ys);
  const span = top - bottom || 1;
  const X = (d: number) => pad.l + ((d - x0) / (x1 - x0)) * (W - pad.l - pad.r);
  const Y = (v: number) => pad.t + ((top + span * 0.08 - v) / (span * 1.16)) * (H - pad.t - pad.b);
  const first = drawn[0]!.points[0]!.date;
  const last = drawn.flatMap((l) => l.points).reduce((a, p) => (p.date > a ? p.date : a), first);
  return (
    <svg className="pmcc-pnl" viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label="P&L since entry">
      <line x1={pad.l} x2={W - pad.r} y1={Y(0)} y2={Y(0)} className="study-spark-zero" />
      <text x={4} y={Y(0) + 4} className="pmcc-axis">$0</text>
      {top > 0 ? <text x={4} y={Y(top) + 4} className="pmcc-axis">{fmtCash(top)}</text> : null}
      {bottom < 0 ? <text x={4} y={Y(bottom) + 4} className="pmcc-axis">{fmtCash(bottom)}</text> : null}
      {marks.map((m, i) => (
        <g key={i}>
          <line x1={X(day(m.date))} x2={X(day(m.date))} y1={pad.t} y2={H - pad.b} className="pmcc-mark" />
          {marks.length <= 3 ? (
            <text x={X(day(m.date)) + 3} y={pad.t + 8} className="pmcc-mark-label">
              {m.label}
            </text>
          ) : null}
        </g>
      ))}
      {drawn.map((l) => {
        const end = l.points[l.points.length - 1]!;
        return (
          <g key={l.key}>
            <polyline points={l.points.map((p) => `${X(day(p.date)).toFixed(1)},${Y(p.net).toFixed(1)}`).join(" ")} fill="none" stroke={l.colour} strokeWidth={2.2} />
            <circle cx={X(day(end.date))} cy={Y(end.net)} r={3.5} fill={l.colour} />
          </g>
        );
      })}
      <text x={pad.l} y={H - 6} className="pmcc-axis">{first}</text>
      <text x={W - pad.r} y={H - 6} className="pmcc-axis" textAnchor="end">{last}</text>
    </svg>
  );
}

export interface PmccStep {
  date: string | null;
  role: string;
  price: string | null;
  cash: number | null;
  what: string;
}

/** The position's chain from its tracker: each long lot bought, then every short sold and how it ended. */
export function pmccSteps(t: PmccTracker): PmccStep[] {
  const steps: PmccStep[] = t.longLots.map((l) => ({
    date: l.opened,
    role: "open",
    price: null,
    // the tracker states the long's cost as signed cash already (paid: negative)
    cash: l.cost,
    what: `buy ${l.quantity} ${l.expiration} ${l.strike}C${l.status !== "open" ? ` · ${l.status}` : ""}`,
  }));
  const shorts = [...t.shorts].sort((a, b) => a.n - b.n);
  shorts.forEach((s, i) => {
    steps.push({
      date: s.opened,
      role: i === 0 ? "sell" : "roll",
      price: s.sold === null ? null : `${s.sold.toFixed(2)} cr`,
      cash: s.entry,
      what: `sell ${s.expiration} ${s.strike}C`,
    });
    if (s.closed !== null) {
      steps.push({
        date: s.closed,
        role: s.how === "expired" ? "expired" : "close",
        price: s.bought === null ? null : `${s.bought.toFixed(2)} db`,
        cash: s.exit,
        what: `${s.how ?? "closed"}${s.why ? ` · ${s.why}` : ""} · short net ${fmtMoney(s.net)}`,
      });
    }
  });
  return steps;
}

const DOT: Record<string, string> = { open: "open", sell: "open", roll: "roll", close: "close", expired: "roll" };

function PositionTab({ positionId, colour }: { positionId: string; colour: string }) {
  const { data, isLoading } = usePmccTracker(positionId);
  if (isLoading) return <p className="muted">Reading the tracker…</p>;
  if (data === undefined || !data.ok || data.data === null) return <p className="muted">{data?.error ?? "The tracker has no answer for this position."}</p>;
  const t = data.data;
  const h = t.header;
  const marks = t.shorts.filter((s) => s.closed !== null && s.how !== "expired").map((s) => ({ date: s.closed!, label: s.how ?? "closed" }));
  return (
    <div className="pmcc-pos">
      <div className="pmcc-pos-main">
        <section className="pmcc-panel">
          <div className="pmcc-panel-head">
            <span className="lt-label">P&amp;L since entry · each week's close, the last now</span>
            <span className={`pmcc-sym-net ${tone(h.net)}`}>{fmtMoney(h.net)}</span>
          </div>
          <PnlChart lines={[{ key: positionId, label: t.position.arm, colour: h.net !== null && h.net < 0 ? "var(--neg)" : colour, points: pnlPoints(t) }]} marks={marks} />
          {marks.length > 0 ? <span className="muted pmcc-mark-note">dashed: a short closed early (rolled or bought back)</span> : null}
        </section>
        <span className="lt-label">the chain</span>
        <ol className="pos-chain" aria-label="chain">
          {pmccSteps(t).map((s, i) => (
            <li key={i} className={`pos-step pos-step-${DOT[s.role] ?? "open"}`}>
              <span className="pos-dot" aria-hidden="true" />
              <div className="pos-step-body">
                <div className="pos-step-head">
                  {s.date !== null ? <span className="pos-step-date">{s.date}</span> : null}
                  <span className="pos-role">{s.role}</span>
                  <span className="pos-step-price">{s.price ?? ""}</span>
                  <span className={`pos-step-cash ${tone(s.cash)}`}>{s.cash === null ? "" : fmtCash(s.cash)}</span>
                </div>
                <span className="pos-step-legs">{s.what}</span>
              </div>
            </li>
          ))}
          <li className="pos-step pos-step-now">
            <span className="pos-dot" aria-hidden="true" />
            <div className="pos-step-body">
              <div className="pos-step-now-line">
                <span>now · long worth <span className="pos-num">{fmtMoney(h.longValue)}</span></span>
                <span className={`pos-num ${tone(h.net)}`}>{fmtMoney(h.net)}</span>
              </div>
            </div>
          </li>
        </ol>
      </div>
      <aside className="pmcc-pos-side">
        <dl className="md-kv">
          <div><dt>long cost</dt><dd>{fmtMoney(h.longCost)}</dd></div>
          <div><dt>long now</dt><dd>{fmtMoney(h.longValue)}</dd></div>
          <div><dt>shorts realised</dt><dd className={tone(h.shortRealised)}>{fmtMoney(h.shortRealised)}</dd></div>
          <div><dt>short open</dt><dd className={tone(h.shortOpen)}>{fmtMoney(h.shortOpen)}</dd></div>
          <div><dt>costs</dt><dd>{fmtMoney(h.costs?.total ?? null)}</dd></div>
          <div><dt>net</dt><dd className={tone(h.net)}>{fmtMoney(h.net)}</dd></div>
          <div><dt>days in trade</dt><dd>{h.daysInTrade ?? "—"}</dd></div>
          {h.exits ? (
            <>
              <div><dt>room to the stop</dt><dd>{fmtMoney(h.exits.stopRoom)}</dd></div>
              <div><dt>long closes on</dt><dd>{h.exits.longCloseOn}</dd></div>
            </>
          ) : null}
          {t.currentShort ? (
            <div><dt>short extrinsic now</dt><dd>{t.currentShort.extrinsicNow === null ? "—" : t.currentShort.extrinsicNow.toFixed(2)}</dd></div>
          ) : null}
        </dl>
        {t.integrity.unpricedWeeks > 0 ? <p className="muted">{t.integrity.unpricedWeeks} week(s) unpriced: left off the chart, never drawn as zero.</p> : null}
      </aside>
    </div>
  );
}

function AllArmsTab({ positions }: { positions: PmccOpenPosition[] }) {
  const results = useQueries({
    queries: positions.map((p) => ({
      queryKey: ["pmcc-tracker", p.positionId],
      queryFn: async () => {
        const res = await fetch(`/api/pmcc/tracker?position=${encodeURIComponent(p.positionId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as PmccBridged<PmccTracker>;
      },
      refetchInterval: 30_000,
    })),
  });
  const lines: PnlLine[] = positions.flatMap((p, i) => {
    const t = results[i]?.data;
    return t?.ok && t.data ? [{ key: p.positionId, label: p.arm, colour: LINE_COLOURS[i % LINE_COLOURS.length]!, points: pnlPoints(t.data) }] : [];
  });
  return (
    <section className="pmcc-panel">
      <span className="lt-label">P&amp;L since entry, each arm on this symbol · the same market path</span>
      <PnlChart lines={lines} />
      <div className="meic-arm-legend">
        {lines.map((l) => (
          <span key={l.key}>
            <span className="meic-arm-swatch" style={{ background: l.colour }} />
            {l.label}
          </span>
        ))}
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Within one symbol the arms see the same prices, so the gap between lines is their rules. An arm that entered later starts later.
      </p>
    </section>
  );
}

function SymbolDialog({
  symbols,
  open,
  index,
  openId,
  onStep,
  onClose,
}: {
  symbols: string[];
  open: PmccOpenPosition[];
  index: number;
  openId: string;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const symbol = symbols[index]!;
  const here = open.filter((p) => p.symbol === symbol).sort((a, b) => ARM_ORDER.indexOf(a.arm) - ARM_ORDER.indexOf(b.arm));
  const start = here.some((p) => p.positionId === openId) ? openId : (here[0]?.positionId ?? "all");
  const [tab, setTab] = useState<string>(start);
  const current = here.some((p) => p.positionId === tab) || tab === "all" ? tab : start;
  const tabs: LightboxTab[] = [
    ...(here.length > 1 ? [{ id: "all", label: "all arms" }] : []),
    ...here.map((p) => ({ id: p.positionId, label: p.arm })),
  ];
  const spot = here.find((p) => p.currentSpot !== null)?.currentSpot ?? null;
  return (
    <CardLightbox count={symbols.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={`PMCC · paper${spot !== null ? ` · spot ${spot.toFixed(2)}` : ""}`}
          title={symbol}
          chips={
            <>
              {here.map((p) => (
                <span key={p.positionId} className={`chip ${tone(p.unrealisedNet)}`}>
                  {p.arm} {fmtMoney(p.unrealisedNet)}
                </span>
              ))}
            </>
          }
          tabs={tabs}
          tab={current}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {current === "all" ? (
            <AllArmsTab positions={here} />
          ) : (
            <PositionTab key={current} positionId={current} colour={LINE_COLOURS[Math.max(0, here.findIndex((p) => p.positionId === current)) % LINE_COLOURS.length]!} />
          )}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The board: a column per arm, a card per symbol it holds; a card opens its symbol. */
export function PmccArmBoard({ symbol }: { symbol: string | null }) {
  const { data, isLoading, isError } = usePmcc(null);
  const lightbox = useOpenParam("pos");
  if (isError) return <p className="pnl-neg">The PMCC ledger could not be read.</p>;
  if (isLoading || data === undefined) return <p className="muted">Reading the PMCC ledger…</p>;
  const open = data.openPositions.filter((p) => symbol === null || p.symbol === symbol);
  if (open.length === 0) return <p className="muted">No open PMCC position{symbol !== null ? ` on ${symbol}` : ""}.</p>;
  const cols = armColumns(open);
  const symbols = [...new Set(open.map((p) => p.symbol))].sort();
  const openPos = open.find((p) => p.positionId === lightbox.open);
  const index = openPos ? symbols.indexOf(openPos.symbol) : -1;
  return (
    <>
      <p className="muted pmcc-board-note">Read per symbol, never pooled: an arm shows each symbol's net, not a total across them.</p>
      <div className="pmcc-board">
        {cols.map((c) => (
          <section key={c.arm} className="pmcc-col">
            <header className="pmcc-head">
              <span className="pmcc-sym-top">
                <span className="pmcc-arm-name">{c.arm}</span>
                <span className="muted">
                  {c.positions.length} symbol{c.positions.length === 1 ? "" : "s"}
                </span>
              </span>
              <span className="pmcc-rule">{PMCC_ARM_RULE[c.arm] ?? ""}</span>
              <SymbolBars positions={c.positions} />
              <span className="pmcc-rule">
                {c.exposed === 0 ? "no short has been assignment-exposed" : `${c.exposed} short${c.exposed === 1 ? "" : "s"} assignment-exposed on some marks`}
              </span>
            </header>
            {c.positions.map((p) => (
              <SymbolCard key={p.positionId} p={p} onOpen={() => lightbox.show(p.positionId)} />
            ))}
          </section>
        ))}
      </div>
      {openPos && index >= 0 ? (
        <SymbolDialog
          key={openPos.symbol}
          symbols={symbols}
          open={open}
          index={index}
          openId={openPos.positionId}
          onStep={(to) => {
            const next = open.filter((p) => p.symbol === symbols[to]).sort((a, b) => ARM_ORDER.indexOf(a.arm) - ARM_ORDER.indexOf(b.arm))[0];
            if (next) lightbox.step(next.positionId);
          }}
          onClose={lightbox.close}
        />
      ) : null}
    </>
  );
}
