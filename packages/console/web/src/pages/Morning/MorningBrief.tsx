import type { CSSProperties, ReactNode } from "react";
import type { MorningFuture, MorningPack } from "@console/shared";
import { dayLabel, gateMarks, sectorCounts, sparkPoints, upcomingDays, type MorningView } from "./brief";
import { chartHref } from "../Chart/chartRoute";

/**
 * The morning report's Brief tab: the morning on one screen. The verdict strip (phase, each gate as a
 * chip, the deployment score beside it as record-only), four tiles that each open their detail in a
 * lightbox, the prior session's profile levels, the next dates on the calendar, and the sector board
 * as one strip. Every figure is the pack's, as on the other tabs; this tab only puts the headline of
 * each block in one place.
 */

export type BriefDetail = "premarket" | "gamma" | "vol" | "moves";

function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signed(v: number | null | undefined, digits = 2, unit = "%"): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}${unit}`;
}

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pnl-pos" : "pnl-neg";
}

export function ordinal(n: number): string {
  const r = Math.round(n);
  const t = r % 100;
  if (t >= 11 && t <= 13) return `${String(r)}th`;
  return `${String(r)}${["th", "st", "nd", "rd"][r % 10] ?? "th"}`;
}

function phaseTone(phase: string | undefined): string {
  if (phase === "green") return "chip-ok";
  if (phase === "yellow") return "chip-warn";
  return "chip-missing";
}

/** "/ES" from the contract's product, falling back to the pack's key. */
function contract(key: string, f: MorningFuture): string {
  return `/${(f.product ?? key).toUpperCase()}`;
}

function FutureChange({ f }: { f: MorningFuture }) {
  if (f.changeVsPriorClosePct === null) return <span className="muted">{f.changeReason === "no_live_print" ? "no live print" : "—"}</span>;
  return <span className={tone(f.changeVsPriorClosePct)}>{signed(f.changeVsPriorClosePct)}</span>;
}

function Tile({ kicker, onOpen, label, children }: { kicker: ReactNode; onOpen: () => void; label: string; children: ReactNode }) {
  return (
    <button type="button" className="card mb-tile" onClick={onOpen} aria-label={label}>
      <span className="mb-kicker mb-kicker-row">
        {kicker}
        <span className="mb-more">details ›</span>
      </span>
      {children}
    </button>
  );
}

function PremarketTile({ pack, onOpen }: { pack: MorningPack; onOpen: () => void }) {
  const fut = pack.premarket?.futures ?? {};
  const es = fut["es"];
  const others = ["nq", "rty"].filter((k) => fut[k] !== undefined);
  const spx = pack.readings["spx"];
  return (
    <Tile kicker="Pre-market futures" onOpen={onOpen} label="Open the pre-market detail">
      {es ? (
        <>
          <span className="mb-big">
            {contract("es", es)} {num(es.value)} <FutureChange f={es} />
          </span>
          {others.length > 0 && (
            <span className="mb-line">
              {others.map((k, i) => (
                <span key={k}>
                  {i > 0 && " · "}
                  {contract(k, fut[k]!)} <FutureChange f={fut[k]!} />
                </span>
              ))}
            </span>
          )}
          <span className="mb-foot">vs prior settle</span>
        </>
      ) : (
        <>
          <span className="mb-big">SPX {num(spx?.value)}</span>
          <span className="mb-foot">{spx?.basis === "live" ? "live pre-open" : `prior (${spx?.session ?? "—"})`} · no futures tape in this pack</span>
        </>
      )}
    </Tile>
  );
}

function GammaTile({ pack, onOpen }: { pack: MorningPack; onOpen: () => void }) {
  const l = pack.levels;
  return (
    <Tile kicker={`Gamma${l?.symbol ? ` · ${l.symbol}` : ""}`} onOpen={onOpen} label="Open the gamma levels">
      {l === null ? (
        <span className="mb-big muted">no levels</span>
      ) : (
        <>
          <span className={`mb-big ${l.referenceBasis === "live" ? "" : "muted"}`}>ref {num(l.referencePrice)}</span>
          <span className="mb-line">
            flip {num(l.zeroGamma, 0)} · walls <span className="pnl-neg">{num(l.putWall, 0)}</span>–<span className="pnl-pos">{num(l.callWall, 0)}</span>
          </span>
          <span className="mb-foot">{l.referenceBasis ?? (l.session ? `as of ${l.session}` : "—")}</span>
        </>
      )}
    </Tile>
  );
}

function VolTile({ pack, onOpen }: { pack: MorningPack; onOpen: () => void }) {
  const v = pack.volRegime;
  const vix = pack.readings["vix"];
  const vvix = pack.readings["vvix"];
  const pVix = v?.percentiles["vix"];
  const pVvix = v?.percentiles["vvix"];
  const pts = v ? sparkPoints(v.curve, 120, 34) : [];
  const front = v?.curve.find((p) => p.symbol === "VIX");
  return (
    <Tile kicker={`Vol curve${v?.shape ? ` · ${v.shape}` : ""}`} onOpen={onOpen} label="Open the vol term structure">
      <span className="mb-big mb-big-row">
        VIX {num(vix?.value ?? front?.value)}
        {pts.length > 0 && (
          <svg className="mb-spark" width="120" height="34" viewBox="0 0 120 34" role="img" aria-label="VIX term structure">
            <polyline points={pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")} />
            {pts.map((p) => (
              <circle key={p.symbol} cx={p.x} cy={p.y} r={p.symbol === "VIX" ? 3.5 : 2} className={p.symbol === "VIX" ? "mb-spark-now" : ""} />
            ))}
          </svg>
        )}
      </span>
      <span className="mb-line">
        VIX/VIX3M {num(v?.vixVix3mRatio, 2)} · VVIX {num(vvix?.value ?? pVvix?.value)}
      </span>
      <span className="mb-foot">
        {pVix?.percentile != null ? `VIX ${ordinal(pVix.percentile)} pctile of ${String(pVix.samples ?? 0)}` : "VIX percentile —"}
        {pVvix?.percentile != null ? ` · VVIX ${ordinal(pVvix.percentile)}` : ""}
      </span>
    </Tile>
  );
}

function MovesTile({ pack, onOpen }: { pack: MorningPack; onOpen: () => void }) {
  const m = pack.moves;
  const y = pack.yields;
  const em = m?.weeklyExpectedMove;
  const ten = y?.yields["10y"];
  return (
    <Tile kicker="Expected move · week" onOpen={onOpen} label="Open the expected move and yields">
      <span className="mb-big">
        {em?.pct == null ? "—" : `±${num(em.pct)}%`} {em?.points != null && <span className="muted mb-sub">±{num(em.points, 1)} pts</span>}
      </span>
      <span className="mb-line">
        realised {m?.realizedVol.pct == null ? "—" : `${num(m.realizedVol.pct)}%`} · VIX−RV{" "}
        <span className={tone(m?.vixMinusRealized)}>{signed(m?.vixMinusRealized, 2, "")}</span>
      </span>
      <span className="mb-foot">
        {ten == null ? "no Treasury curve" : `10y ${num(ten)}% (${signed(y?.changeBp["10y"] ?? null, 0, " bp")})`}
        {y?.spread2s10sBp != null ? ` · 2s10s ${signed(y.spread2s10sBp, 0, " bp")}` : ""}
      </span>
    </Tile>
  );
}

function VerdictStrip({ pack }: { pack: MorningPack }) {
  const ph = pack.phase;
  const d = pack.deployment;
  const marks = gateMarks(pack.gates);
  return (
    <div className="card mb-verdict">
      <div className="mb-verdict-main">
        <div className="mb-verdict-head">
          <span className="mb-kicker">Session phase</span>
          <span className={`chip ${phaseTone(ph?.phase)} mb-phase`}>{ph ? ph.phase.toUpperCase() : "unknown"}</span>
          {ph && ph.gatesMet !== null && ph.gatesMeasured !== null && (
            <span className="mb-count">
              {ph.gatesMet} of {ph.gatesMeasured} gates met
            </span>
          )}
        </div>
        <p className={`mb-reason ${ph?.reason ? "" : "muted"}`}>{ph?.reason ?? "The pack carries no phase verdict."}</p>
        {marks.length > 0 && (
          <div className="mb-gates">
            {marks.map((g) => (
              <span key={g.id} className={`mb-gate mb-gate-${g.tone}`} title={g.detail ?? undefined}>
                {g.mark} {g.label}
              </span>
            ))}
          </div>
        )}
      </div>
      {d && (
        <div className="mb-deploy">
          <span className="mb-kicker">Deployment score</span>
          <span className="mb-score">
            {d.score === null ? "—" : num(d.score, 1)}
            <span className="muted"> / 100</span>
          </span>
          <span className="mb-foot">
            record-only
            {d.signalsMeasured !== null && d.signalsTotal !== null ? ` · ${String(d.signalsMeasured)} of ${String(d.signalsTotal)} signals` : ""}
          </span>
        </div>
      )}
    </div>
  );
}

function ProfileMini({ pack }: { pack: MorningPack }) {
  const prof = pack.profile;
  if (!prof) return null;
  const rows = Object.entries(prof.symbols);
  const session = rows.find(([, s]) => s.session !== null)?.[1].session ?? null;
  const f = (v: number | null): string => num(v);
  return (
    <div className="card mb-panel">
      <div className="mb-panel-head">
        <span className="mb-kicker">Market profile · prior session{session ? ` (${dayLabel(session, false)})` : ""}</span>
      </div>
      {rows.length === 0 ? (
        <p className="muted">Not measured ({prof.reason ?? "no profile bars"}).</p>
      ) : (
        <div className="mb-pgrid">
          <span />
          <span className="mb-colhead">POC</span>
          <span className="mb-colhead">value area</span>
          <span className="mb-colhead mb-hide-narrow">untested POCs</span>
          {rows.map(([sym, s]) =>
            s.session === null ? (
              <span key={sym} className="mb-prow">
                <span>{sym}</span>
                <span className="muted mb-prow-wide">not measured ({s.reason ?? "—"})</span>
              </span>
            ) : (
              <span key={sym} className="mb-prow">
                <a href={chartHref(sym, "15m", { day: s.session })}>{sym}</a>
                <span className="mb-poc">{f(s.poc)}</span>
                <span>
                  {f(s.val)}–{f(s.vah)}
                </span>
                <span className="muted mb-hide-narrow">
                  {s.untestedPocs.length === 0
                    ? "none"
                    : s.untestedPocs
                        .slice(0, 3)
                        .map((u) => f(u.poc))
                        .join(" · ")}
                </span>
              </span>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function CalendarMini({ pack, onView }: { pack: MorningPack; onView: (v: MorningView) => void }) {
  const c = pack.calendar;
  const days = upcomingDays(c, pack.session, 3);
  return (
    <div className="card mb-panel">
      <div className="mb-panel-head">
        <span className="mb-kicker">On the calendar</span>
        {c?.isFomcDay && <span className="chip chip-warn">FOMC today</span>}
        <button type="button" className="mb-link" onClick={() => onView("calendar")}>
          Calendar tab →
        </button>
      </div>
      {days.length === 0 ? (
        <p className="muted">{c === null ? "No calendar facts in this pack." : "Nothing scheduled in the week ahead."}</p>
      ) : (
        <div className="mb-days">
          {days.map((d) => {
            const priced = d.earnings.filter((e) => e.movePct !== null).slice(0, 2);
            const rest = d.earnings.filter((e) => !priced.includes(e));
            const parts: ReactNode[] = [
              ...d.releases,
              ...priced.map((e) => `${e.symbol} ±${num(e.movePct, 1)}%`),
              ...(rest.length > 0 ? [rest.map((e) => e.symbol).join(" · ")] : []),
            ];
            return (
              <div key={d.date} className="mb-day">
                <span className={`mb-daylabel ${d.today ? "mb-today" : ""}`}>{dayLabel(d.date, d.today)}</span>
                <span className="mb-dayitems">
                  {d.earnings.length > 0 && d.releases.length === 0 ? "earnings " : ""}
                  {parts.map((p, i) => (
                    <span key={i}>
                      {i > 0 && " · "}
                      {p}
                    </span>
                  ))}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SectorStrip({ pack, onView }: { pack: MorningPack; onView: (v: MorningView) => void }) {
  const s = pack.sectors;
  if (s === null || s.board.length === 0) return null;
  const board = [...s.board].sort((a, b) => {
    if (a.changePct === null && b.changePct === null) return 0;
    if (a.changePct === null) return 1;
    if (b.changePct === null) return -1;
    return b.changePct - a.changePct;
  });
  const peak = Math.max(0.01, ...board.map((r) => Math.abs(r.changePct ?? 0)));
  const counts = sectorCounts(board);
  const session = board.find((r) => r.session !== null)?.session ?? null;
  return (
    <div className="card mb-panel">
      <div className="mb-panel-head">
        <span className="mb-kicker">Sectors{session ? ` · ${dayLabel(session, false)}` : ""}</span>
        <span className="mb-count">
          {counts.up} up · {counts.down} down{counts.unmeasured > 0 ? ` · ${String(counts.unmeasured)} unmeasured` : ""}
        </span>
        <button type="button" className="mb-link" onClick={() => onView("tape")}>
          Tape tab →
        </button>
      </div>
      <div className="mb-sectors" style={{ "--mb-n": String(Math.min(board.length, 11)) } as CSSProperties}>
        {board.map((r) => {
          const c = r.changePct;
          const cls = c === null ? "mb-sector-none" : c > 0 ? "mb-sector-up" : c < 0 ? "mb-sector-down" : "";
          const weight = c === null ? 0 : Math.round(8 + (Math.abs(c) / peak) * 30);
          return (
            <div key={r.symbol} className={`mb-sector ${cls}`} style={{ "--mb-w": `${String(weight)}%` } as CSSProperties} title={r.sector ?? undefined}>
              <span className="mb-sector-sym">{r.symbol}</span>
              <span>{signed(c)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function MorningBrief({ pack, onOpen, onView }: { pack: MorningPack; onOpen: (d: BriefDetail) => void; onView: (v: MorningView) => void }) {
  return (
    <div className="mb">
      <VerdictStrip pack={pack} />
      <div className="mb-tiles">
        <PremarketTile pack={pack} onOpen={() => onOpen("premarket")} />
        <GammaTile pack={pack} onOpen={() => onOpen("gamma")} />
        <VolTile pack={pack} onOpen={() => onOpen("vol")} />
        <MovesTile pack={pack} onOpen={() => onOpen("moves")} />
      </div>
      <div className="mb-pair">
        <ProfileMini pack={pack} />
        <CalendarMini pack={pack} onView={onView} />
      </div>
      <SectorStrip pack={pack} onView={onView} />
    </div>
  );
}
