import { useState } from "react";
import { Link } from "react-router-dom";
import type { TradingMode } from "@console/shared";
import { AttemptTimeline, useAttempts, type ArmRailEntry, type AttemptsScope } from "../../components/Attempts";
import { AttemptsTab } from "../../components/AttemptsTab";
import { ARM_COLORS, OUTCOME_LABEL_OF } from "../../components/chart/tokens";
import { fmtCash } from "../../lib/format";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { controlScaleOf } from "../Backtest/StudyCards";
import { MeicForestCard, useMeicForest, type ForestArm } from "./MeicForestCard";
import { MeicPositionsTable, type MeicScope } from "./MeicTables";

/**
 * MEIC's arms board (2026-11-04, the user's pick "A" off the mock-up canvas): a large tile with every
 * arm's book drawn together (it opens the forest tab), then one tile per arm. A tile is the arm's
 * realised net, its payoff at expiry in small, what it entered, stopped and holds, and its entry
 * checks: fills of attempts and the refusal that turned most of them away. A tile opens the shared
 * lightbox on that arm: its forest with its attempt timeline beneath, its attempts in full (rail,
 * timeline, strike occupancy), and its trades.
 *
 * Two reads the page already makes, nothing new: the forest (`/api/meic/forest`) and the attempts
 * (`/api/meic/attempts`), both under the page's own scope. Payoff is at expiry, never a mark. After
 * the close no book is open, so the tiles draw the day's book as entered, the forest card's own rule.
 *
 * MEIC's control is a sampling arm (#829): it enters on most checks, hundreds of condors a session.
 * While it trades on another scale (`controlScaleOf`, the Backtest tiles' rule) its tile is dashed and
 * says so, it sorts after the arms, and the large tile leaves it out of both the drawing and the net.
 */

export interface ArmTile {
  arm: string;
  colour: string;
  /** Realised today; null when the forest has no outcome for the arm. */
  realised: number | null;
  entered: number;
  stopped: number;
  open: number;
  prices: number[];
  pnl: number[];
  attempts: number | null;
  fills: number | null;
  /** The refusal that turned away the most checks, and its share of the arm's checks. */
  refusal: { reason: string; share: number } | null;
  /** True for control while it trades on another scale. */
  otherScale: boolean;
}

function dominantRefusal(r: ArmRailEntry | undefined): ArmTile["refusal"] {
  if (r === undefined || r.attempts === 0) return null;
  const top = Object.entries(r.refusals).sort((a, b) => b[1] - a[1])[0];
  if (top === undefined || top[1] === 0) return null;
  return { reason: OUTCOME_LABEL_OF[top[0]] ?? top[0].replace(/_/g, " "), share: top[1] / r.attempts };
}

/**
 * One tile per arm the forest or the attempts name, in the forest's order for colour (the forest
 * card's own colours), ordered by realised net high to low; control on another scale goes last.
 */
export function armTiles(forest: ForestArm[], rail: ArmRailEntry[]): ArmTile[] {
  const names = [...forest.map((a) => a.arm), ...rail.map((r) => r.arm).filter((a) => !forest.some((f) => f.arm === a))];
  const entered = Object.fromEntries(names.map((n) => [n, forest.find((f) => f.arm === n)?.outcome?.entered ?? rail.find((r) => r.arm === n)?.fills ?? null]));
  const scaled = controlScaleOf(entered) !== null;
  const tiles = names.map((arm, i): ArmTile => {
    const f = forest.find((a) => a.arm === arm);
    const r = rail.find((x) => x.arm === arm);
    return {
      arm,
      colour: ARM_COLORS[i % ARM_COLORS.length]!,
      realised: f?.outcome?.realisedNet ?? null,
      entered: f?.outcome?.entered ?? r?.fills ?? 0,
      stopped: f?.outcome?.stopped ?? 0,
      open: f?.outcome?.open ?? 0,
      prices: f?.prices ?? [],
      pnl: f?.pnl ?? [],
      attempts: r?.attempts ?? null,
      fills: r?.fills ?? null,
      refusal: dominantRefusal(r),
      otherScale: scaled && arm === "control",
    };
  });
  return tiles.sort((a, b) => {
    if (a.otherScale !== b.otherScale) return a.otherScale ? 1 : -1;
    return (b.realised ?? -Infinity) - (a.realised ?? -Infinity);
  });
}

/** Payoff lines on one small chart: a shared y scale, zero dashed, spot marked. */
function Payoff({ lines, prices, spot, tall }: { lines: Array<{ arm: string; colour: string; pnl: number[]; dashed?: boolean }>; prices: number[]; spot: number | null; tall?: boolean }) {
  const drawn = lines.filter((l) => l.pnl.length === prices.length && l.pnl.length > 1);
  if (drawn.length === 0 || prices.length < 2) return <div className="meic-arm-nochart muted">no book to draw</div>;
  const W = 200;
  const H = 48;
  const xMin = prices[0]!;
  const xMax = prices[prices.length - 1]!;
  const values = drawn.flatMap((l) => l.pnl);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const x = (p: number) => ((p - xMin) / (xMax - xMin || 1)) * W;
  const y = (v: number) => 2 + ((top - v) / span) * (H - 4);
  return (
    <svg className={tall ? "meic-arm-payoff is-tall" : "meic-arm-payoff"} viewBox={`0 0 ${String(W)} ${String(H)}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="study-spark-zero" vectorEffect="non-scaling-stroke" />
      {spot !== null && spot >= xMin && spot <= xMax ? <line x1={x(spot)} x2={x(spot)} y1={0} y2={H} className="meic-arm-spot" vectorEffect="non-scaling-stroke" /> : null}
      {drawn.map((l) => (
        <polyline
          key={l.arm}
          points={l.pnl.map((v, i) => `${x(prices[i]!).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
          fill="none"
          stroke={l.colour}
          strokeWidth={1.75}
          strokeDasharray={l.dashed ? "4 3" : undefined}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

function money(v: number | null): string {
  return v === null ? "—" : fmtCash(v);
}

function toneOf(v: number | null): string {
  return v === null ? "muted" : v < 0 ? "pnl-neg" : v > 0 ? "pnl-pos" : "";
}

export function ArmTileView({ t, spot, onOpen }: { t: ArmTile; spot: number | null; onOpen: () => void }) {
  const cls = ["study-tile", "meic-arm-tile", t.realised !== null && t.realised < 0 ? "is-neg" : "", t.otherScale ? "is-other-scale" : ""].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} onClick={onOpen} aria-label={`${t.arm}: realised ${money(t.realised)}`}>
      <span className="meic-arm-top">
        <span className="meic-arm-name">{t.arm}</span>
        <span className={`meic-arm-net ${toneOf(t.realised)}`}>{money(t.realised)}</span>
      </span>
      <Payoff lines={[{ arm: t.arm, colour: t.colour, pnl: t.pnl, dashed: t.otherScale }]} prices={t.prices} spot={spot} />
      <span className="meic-arm-counts">
        {t.otherScale ? `sampling arm · entered ${t.entered} · another scale` : `entered ${t.entered} · stopped ${t.stopped} · open ${t.open}`}
      </span>
      <span className="meic-arm-checks">
        <span className="meic-arm-checks-line">
          <span>{t.attempts === null ? "no checks recorded" : `${t.fills ?? 0} filled of ${t.attempts} checks`}</span>
          <span>{t.otherScale ? "not ranked" : t.refusal === null ? "" : `${t.refusal.reason} ${Math.round(t.refusal.share * 100)}%`}</span>
        </span>
        <span className="meic-arm-bar" aria-hidden="true">
          <span style={{ width: `${t.otherScale || t.refusal === null ? 0 : Math.round(t.refusal.share * 100)}%` }} />
        </span>
      </span>
    </button>
  );
}

type ArmTab = "forest" | "attempts" | "trades";

function ArmDialog({
  tiles,
  index,
  mode,
  scope,
  attemptsScope,
  onStep,
  onClose,
}: {
  tiles: ArmTile[];
  index: number;
  mode: TradingMode;
  scope: MeicScope;
  attemptsScope: AttemptsScope;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<ArmTab>("forest");
  const t = tiles[index];
  if (t === undefined) return null;
  const armScope: MeicScope = { ...scope, profile: t.arm };
  const armAttempts: AttemptsScope = { ...attemptsScope, arm: t.arm };
  const tabs: LightboxTab<ArmTab>[] = [
    { id: "forest", label: "forest" },
    { id: "attempts", label: "attempts" },
    { id: "trades", label: "trades" },
  ];
  return (
    <CardLightbox count={tiles.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={`MEIC · ${mode} · ${scope.day ?? "latest session"}`}
          title={t.arm}
          chips={
            <>
              <span className={`chip ${toneOf(t.realised)}`}>realised {money(t.realised)}</span>
              <span className="chip">
                entered {t.entered} · stopped {t.stopped} · open {t.open}
              </span>
              {t.otherScale ? <span className="chip chip-warn">sampling arm: another scale</span> : null}
            </>
          }
          tabs={tabs}
          tab={tab}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {tab === "forest" ? (
            <div className="cards cards-wide">
              <MeicForestCard mode={mode} scope={armScope} />
              <AttemptTimeline module="meic" mode={mode} date={scope.day} scope={armAttempts} />
            </div>
          ) : tab === "attempts" ? (
            <AttemptsTab module="meic" mode={mode} date={scope.day} scope={armAttempts} />
          ) : (
            <MeicPositionsTable mode={mode} scope={armScope} />
          )}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The board: the whole book large, then a tile per arm; a tile opens its arm (`?arm=`). */
export function MeicArmCards({ mode, scope, attemptsScope }: { mode: TradingMode; scope: MeicScope; attemptsScope: AttemptsScope }) {
  const forest = useMeicForest(mode, scope, null);
  const attempts = useAttempts("meic", mode, scope.day, attemptsScope);
  const lightbox = useOpenParam("arm");
  const live = (forest.data?.arms.length ?? 0) > 0;
  const forestArms = live ? (forest.data?.arms ?? []) : (forest.data?.asEntered ?? []);
  const tiles = armTiles(forestArms, attempts.data?.arms ?? []);
  const spot = forest.data?.lastSpot ?? null;
  const index = tiles.findIndex((t) => t.arm === lightbox.open);
  if (forest.isLoading || attempts.isLoading) {
    return (
      <div className="study-tiles">
        <div className="study-tile study-hero study-tile-skeleton" />
        <div className="study-tile study-tile-skeleton" />
        <div className="study-tile study-tile-skeleton" />
      </div>
    );
  }
  if (tiles.length === 0) return <p className="muted">No MEIC arm entered or checked an entry on this session.</p>;
  const book = tiles.filter((t) => !t.otherScale);
  const prices = book.find((t) => t.prices.length > 1)?.prices ?? [];
  const realised = book.reduce<number | null>((s, t) => (t.realised === null ? s : (s ?? 0) + t.realised), null);
  const left = tiles.find((t) => t.otherScale);
  return (
    <>
      <div className="study-tiles meic-arm-tiles">
        <Link to="/meic/forest" className="study-tile study-hero meic-arm-hero">
          <span className="meic-arm-top">
            <span className="meic-arm-name">Every arm's book</span>
            <span className={`meic-arm-net ${toneOf(realised)}`}>{money(realised)}</span>
          </span>
          <span className="meic-arm-counts">
            {book.reduce((n, t) => n + t.entered, 0)} condors over {book.length} arm{book.length === 1 ? "" : "s"} · {book.reduce((n, t) => n + t.open, 0)} open ·{" "}
            {book.reduce((n, t) => n + t.stopped, 0)} stopped · realised{left ? `, ${left.arm} left out (another scale)` : ""} · payoff at {live ? "expiry" : "expiry, the day's book as entered"}
          </span>
          <Payoff lines={book.map((t) => ({ arm: t.arm, colour: t.colour, pnl: t.pnl }))} prices={prices} spot={spot} tall />
          <span className="meic-arm-legend">
            {book.map((t) => (
              <span key={t.arm}>
                <span className="meic-arm-swatch" style={{ background: t.colour }} />
                {t.arm}
              </span>
            ))}
            <span className="muted">opens the forest</span>
          </span>
        </Link>
        {tiles.map((t) => (
          <ArmTileView key={t.arm} t={t} spot={spot} onOpen={() => lightbox.show(t.arm)} />
        ))}
      </div>
      {index >= 0 ? (
        <ArmDialog
          tiles={tiles}
          index={index}
          mode={mode}
          scope={scope}
          attemptsScope={attemptsScope}
          onStep={(to) => lightbox.step(tiles[to]?.arm ?? "")}
          onClose={lightbox.close}
        />
      ) : null}
    </>
  );
}
