import path from "node:path";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

/**
 * Which contracts each arm currently holds, and on which side — the data behind
 * the strike-occupancy map, and the read-side mirror of the sign rule.
 *
 * **The leg derivation lives here, once per module, deliberately.** It mirrors
 * `paper.ic_legs` (MEIC) and `fly.position_legs` (flies), and those are already
 * each written once in their own package for the same reason: an IC's wings are
 * not stored as numbers, they are `wing_width` outside the shorts, and the same
 * arithmetic copied into a component would be a third place to disagree about
 * what a book holds. A page that renders occupancy differently from the gate
 * that enforces it is worse than no page.
 *
 * Signs, not columns, carry the meaning. Same-sign stacking is legal and
 * ordinary — two flies sharing a wing, a condor nested inside another — so what
 * matters is which sign sits at a contract, and the thing that must never
 * appear is both signs at one.
 */

export interface OccupancyLeg {
  arm: string;
  right: "P" | "C";
  strike: number;
  /** +1 long, -1 short. */
  sign: number;
  /** How many contracts of that sign sit here — a fly's centre is 2. */
  count: number;
}

export interface OccupancyPayload {
  mode: TradingMode;
  module: "meic" | "flies";
  tradeDate: string | null;
  legs: OccupancyLeg[];
}

function merge(legs: OccupancyLeg[]): OccupancyLeg[] {
  const out = new Map<string, OccupancyLeg>();
  for (const leg of legs) {
    const key = `${leg.arm}|${leg.right}|${leg.strike}|${leg.sign}`;
    const seen = out.get(key);
    if (seen === undefined) out.set(key, { ...leg });
    else seen.count += leg.count;
  }
  return [...out.values()].sort((a, b) => b.strike - a.strike || a.arm.localeCompare(b.arm));
}

/** Port of `fly.position_legs`. Doubled strikes carry count 2, not two rows. */
function flyLegs(row: {
  kind: string;
  side: string;
  center: number;
  wingWidth: number;
  farWidth: number | null;
  arm: string;
}): OccupancyLeg[] {
  const { kind, center: k, wingWidth: w, arm } = row;
  const right: "P" | "C" = row.side.toLowerCase().startsWith("c") ? "C" : "P";
  const put = (strike: number, sign: number, count = 1): OccupancyLeg => ({ arm, right: "P", strike, sign, count });
  const call = (strike: number, sign: number, count = 1): OccupancyLeg => ({ arm, right: "C", strike, sign, count });
  const leg = (strike: number, sign: number, count = 1): OccupancyLeg => ({ arm, right, strike, sign, count });

  if (kind === "iron_fly") {
    // The one flies structure that is not single-type: short a put AND a call at
    // its centre. Exactly why `right` is part of the key — those two shorts are
    // different contracts and neither nets against the other.
    return [put(k - w, 1), put(k, -1), call(k, -1), call(k + w, 1)];
  }
  if (kind === "fly") return [leg(k - w, 1), leg(k, -1, 2), leg(k + w, 1)];
  if (kind === "short_vertical") return [leg(k, -1), leg(right === "P" ? k - w : k + w, 1)];
  if (kind === "long_vertical" || kind === "debit_vertical") {
    return [leg(k, -1), leg(right === "C" ? k - w : k + w, 1)];
  }
  if (kind === "bwb") {
    const f = row.farWidth ?? w;
    const near = right === "P" ? k + w : k - w;
    const far = right === "P" ? k - f : k + f;
    return [leg(near, 1), leg(k, -1, 2), leg(far, 1)];
  }
  return [];
}

export function readOccupancy(
  config: ConsoleConfig,
  module: "meic" | "flies",
  mode: TradingMode,
  day: string | null,
): OccupancyPayload {
  const empty: OccupancyPayload = { mode, module, tradeDate: null, legs: [] };

  if (module === "meic") {
    const file = mode === "live" ? "meic_trades.db" : "paper_trades.db";
    const dbPath = path.join(config.paths.meicDir, file);
    return withReadOnlyDb<OccupancyPayload>(dbPath, empty, (db) => {
      const dayRow = day
        ? { d: day }
        : db.prepare<[], { d: string }>("SELECT MAX(trade_date) AS d FROM ic_trades").get();
      const tradeDate = dayRow?.d ?? null;
      if (tradeDate === null) return empty;
      const rows = db
        .prepare<[string], Record<string, unknown>>(
          `SELECT risk_profile, put_strike, call_strike, wing_width
             FROM ic_trades WHERE trade_date = ? AND status = 'open'`,
        )
        .all(tradeDate);
      const legs: OccupancyLeg[] = [];
      for (const r of rows) {
        const arm = str(r["risk_profile"]) ?? "?";
        const w = num(r["wing_width"]);
        const p = num(r["put_strike"]);
        const c = num(r["call_strike"]);
        // A row without a wing width contributes its shorts alone rather than
        // being dropped — same permissive-in-one-direction rule `ic_legs` keeps.
        if (p !== null) {
          legs.push({ arm, right: "P", strike: p, sign: -1, count: 1 });
          if (w !== null) legs.push({ arm, right: "P", strike: p - w, sign: 1, count: 1 });
        }
        if (c !== null) {
          legs.push({ arm, right: "C", strike: c, sign: -1, count: 1 });
          if (w !== null) legs.push({ arm, right: "C", strike: c + w, sign: 1, count: 1 });
        }
      }
      return { mode, module, tradeDate, legs: merge(legs) };
    });
  }

  const file = mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  return withReadOnlyDb<OccupancyPayload>(dbPath, empty, (db) => {
    const dayRow = day
      ? { d: day }
      : db.prepare<[], { d: string }>("SELECT MAX(trade_date) AS d FROM fly_positions").get();
    const tradeDate = dayRow?.d ?? null;
    if (tradeDate === null) return empty;
    // The WHOLE day, not just what is open: flies complete rather than close, so
    // nothing leaves the book before EOD and every structure entered today still
    // constrains a new entry. Voided rows are excluded — the module has disavowed
    // those as evidence, so they constrain nothing.
    const rows = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT arm, kind, side, center, wing_width, far_width
           FROM fly_positions
          WHERE trade_date = ? AND status != 'voided' AND void_reason IS NULL`,
      )
      .all(tradeDate);
    const legs: OccupancyLeg[] = [];
    for (const r of rows) {
      const center = num(r["center"]);
      const wingWidth = num(r["wing_width"]);
      if (center === null || wingWidth === null) continue;
      legs.push(
        ...flyLegs({
          arm: str(r["arm"]) ?? "?",
          kind: str(r["kind"]) ?? "",
          side: str(r["side"]) ?? "put",
          center,
          wingWidth,
          farWidth: num(r["far_width"]),
        }),
      );
    }
    return { mode, module, tradeDate, legs: merge(legs) };
  });
}
