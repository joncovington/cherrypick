import type {
  ManageAccount,
  ManageLiveAccount,
  ManageLivePayload,
  ManageLiveTrade,
  ManagePmccPosition,
  ManageTrade,
  ManageWheelCheck,
} from "@console/shared";
import { contractKey } from "./AttentionCard";

/**
 * The evaluation's prices moved to the live ones (`/api/manage/live`: the stream cache, two minutes at
 * most in the session). Display only, and pure: a mid or a spot is swapped for its live quote and the
 * figures manage derives from them by arithmetic are redone the way manage does them (intrinsic,
 * extrinsic, a trade's value and P&L, whether the mark is at or under a resting buy-to-close). Every
 * judgement stays the evaluation's: tested, the verdicts, the levels, the bands. Where no live quote
 * is fresh, the evaluation's figure stands.
 */

const EPS = 1e-9;

/** A call's or a put's value at the strike, a share. */
export function intrinsic(spot: number | null, strike: number | null, right: string | null): number | null {
  if (spot === null || strike === null || !spot) return null;
  return right === "P" ? Math.max(0, strike - spot) : Math.max(0, spot - strike);
}

function markOf(live: ManageLivePayload, symbol: string): number | null {
  return live.marks?.[contractKey(symbol)] ?? null;
}

function spotOf(live: ManageLivePayload, underlying: string): number | null {
  return live.spots[underlying] ?? null;
}

/** A trade on its live value when every leg has a fresh quote (the payload's own rule), with the
 *  evaluation's net kept beside it; otherwise the evaluation's trade, unchanged. */
export function liveTrade(t: ManageTrade, l: ManageLiveTrade | undefined): { t: ManageTrade; live: boolean; evaluatedNet: number | null } {
  if (!l || l.value === null || l.pnlGross === null) return { t, live: false, evaluatedNet: t.pnlNet };
  return { t: { ...t, value: l.value, pnlGross: l.pnlGross, pnlNet: l.pnlNet, capture: l.capture }, live: true, evaluatedNet: t.pnlNet };
}

/** The wheel's check on one short, on the live mid and spot: extrinsic and the buy-to-close test
 *  redone as manage does them (wheel.py). `tested` stays manage's. */
export function liveWheelCheck(r: ManageWheelCheck, underlying: string, live: ManageLivePayload): ManageWheelCheck {
  const mark = markOf(live, r.symbol);
  const spotLive = spotOf(live, underlying);
  if (mark === null && spotLive === null) return r;
  const mid = mark ?? r.mid;
  const spot = spotLive ?? r.spot;
  const intr = intrinsic(spot, r.strike, r.right);
  const ext = mid !== null && intr !== null ? mid - intr : null;
  return {
    ...r,
    spot,
    mid,
    intrinsic: intr,
    extrinsic: ext,
    extrinsicDollars: ext === null || r.quantity === null ? null : ext * 100 * -r.quantity,
    markAtOrUnderBtc: r.btcPrice === null || mid === null ? null : mid <= r.btcPrice + EPS,
  };
}

/** The wheel view with each running cycle's checks on live quotes. */
export function liveWheel(a: ManageAccount, live: ManageLivePayload | null): ManageAccount {
  if (live === null || a.wheel === null) return a;
  return {
    ...a,
    wheel: {
      ...a.wheel,
      cycles: a.wheel.cycles.map((c) => (c.today.length ? { ...c, today: c.today.map((r) => liveWheelCheck(r, c.underlying, live)) } : c)),
    },
  };
}

/** A PMCC on live quotes: the spot, the long's and the short's mids, and their intrinsic and
 *  extrinsic as pmcc.py works them (calls). The width check, the roll rules and `tested` stay manage's. */
export function livePmcc(p: ManagePmccPosition, live: ManageLivePayload): ManagePmccPosition {
  const spotLive = spotOf(live, p.underlying);
  const longMark = markOf(live, p.long.symbol);
  const shortMark = markOf(live, p.short.symbol);
  if (spotLive === null && longMark === null && shortMark === null) return p;
  const spot = spotLive ?? p.spot;
  const lmid = longMark ?? p.long.mid;
  const lintr = intrinsic(spot, p.long.strike, "C");
  const lext = lmid !== null && lintr !== null ? lmid - lintr : null;
  const smid = shortMark ?? p.short.mid;
  const sintr = intrinsic(spot, p.short.strike, "C");
  return {
    ...p,
    spot,
    long: { ...p.long, mid: lmid, intrinsic: lintr, extrinsic: lext, extrinsicShare: lext === null || !lmid ? null : lext / lmid },
    short: { ...p.short, mid: smid, intrinsic: sintr, extrinsic: smid !== null && sintr !== null ? smid - sintr : null },
  };
}

/** The account's PMCC view on live quotes. */
export function livePmccs(a: ManageAccount, live: ManageLivePayload | null): ManageAccount {
  if (live === null || a.pmcc === null) return a;
  return { ...a, pmcc: { ...a.pmcc, positions: a.pmcc.positions.map((p) => livePmcc(p, live)) } };
}

/** The live greeks for one account, where the payload has them. */
export function liveAccountOf(live: ManageLivePayload | null, account: string | null): ManageLiveAccount | null {
  return live?.accounts.find((x) => x.account === account) ?? null;
}
