/**
 * MEIC live against paper: each live condor beside the paper
 * arm that trades what live trades -- `live-shadow`, a `follows_live` arm merged onto the `live`
 * block, so it is live's twin by construction -- paired by session and, within a session, by entry
 * order. Paper is read from the first live session on. Once `live-shadow` retires (its `arm_retired`
 * break), each session's mirror is the twin (`shadow_of`) of the arm live ran that session
 * (`mirrorRows`), read from the live ledger's entry attempts: a session whose arm has no twin has no
 * mirror and is listed apart.
 *
 * A paper session with no live row is kept and counted apart: live was not armed that day, or
 * refused. What live sat out is part of the gap between the two, not noise to drop. Money follows
 * the MEIC page: whole-position dollars, net = gross − fees (settlement inside fees), open rows and
 * rows without recorded fees carry no net. The paired totals are the sums of their rows.
 */

import path from "node:path";
import type { MeicLiveGapPayload, MeicLiveGapRow, MeicLiveGapSide } from "@console/shared";
import { meicRiskConfigPath, type ConsoleConfig } from "../config.js";
import { findArmColumn, hasColumn, hasTable, num, readJson, readOnlyDb, str, type DatabaseHandle } from "./db.js";

export const LIVE_ARM = "live";
export const TWIN_ARM = "live-shadow";

const RESOLVED = "status NOT IN ('cancelled','pending','partial_entry')";

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function col(db: DatabaseHandle, name: string): string {
  return hasColumn(db, "ic_trades", name) ? name : "NULL";
}

function side(r: Record<string, unknown>): MeicLiveGapSide {
  const gross = num(r["pnl"]);
  const fees = num(r["fees"]);
  return {
    entry: str(r["entry_time"]),
    symbol: str(r["symbol"]),
    putStrike: num(r["put_strike"]),
    callStrike: num(r["call_strike"]),
    width: num(r["wing_width"]),
    credit: num(r["net_credit"]),
    status: str(r["status"]),
    gross,
    fees,
    net: gross !== null && fees !== null ? round2(gross - fees) : null,
  };
}

/** One arm's resolved rows from `since` on, grouped by session in entry order; null when unreadable. */
function rowsOf(file: string, arm: string, since: string | null): Map<string, MeicLiveGapSide[]> | null {
  const outcome = readOnlyDb(file, (db) => {
    const armCol = findArmColumn(db, "ic_trades");
    if (armCol === null) return new Map<string, MeicLiveGapSide[]>();
    const rows = db
      .prepare<unknown[], Record<string, unknown>>(
        `SELECT trade_date, entry_time, symbol, put_strike, call_strike, wing_width, net_credit, status,
                pnl, fees, ${col(db, "quantity")} AS quantity
           FROM ic_trades
          WHERE ${armCol} = ? AND ${RESOLVED} ${since !== null ? "AND trade_date >= ?" : ""}
          ORDER BY trade_date, entry_time`,
      )
      .all(...(since !== null ? [arm, since] : [arm]));
    const out = new Map<string, MeicLiveGapSide[]>();
    for (const r of rows) {
      const day = str(r["trade_date"]);
      if (day === null) continue;
      out.set(day, [...(out.get(day) ?? []), side(r)]);
    }
    return out;
  });
  return outcome.status === "ok" ? outcome.value : null;
}

const TWIN_META_KEYS = new Set(["enabled", "follows_live", "shadow_of"]);

/**
 * {arm: twin} from the machine's arm registry: each `shadow_of` profile with no override of its own,
 * enabled or not (a retired twin still holds its rows). A twin with an override is an experiment beside
 * the arm, not its mirror. Empty when the registry has none, is absent, or cannot be read.
 */
export function meicTwins(config: ConsoleConfig): Map<string, string> {
  const out = new Map<string, string>();
  if (!config.paths.meicRiskConfig) return out;
  const profiles = readJson(meicRiskConfigPath(config))?.["profiles"];
  if (typeof profiles !== "object" || profiles === null || Array.isArray(profiles)) return out;
  for (const [name, p] of Object.entries(profiles as Record<string, unknown>)) {
    if (name.startsWith("_") || typeof p !== "object" || p === null || Array.isArray(p)) continue;
    const spec = p as Record<string, unknown>;
    const of = spec["shadow_of"];
    if (typeof of !== "string" || of === "") continue;
    if (Object.keys(spec).some((k) => !k.startsWith("_") && !TWIN_META_KEYS.has(k))) continue;
    if (!out.has(of)) out.set(of, name);
  }
  return out;
}

/** The date the unpinned `live-shadow` retired (its `arm_retired` break in the paper ledger), or null. */
function shadowRetiredOn(file: string): string | null {
  const outcome = readOnlyDb(file, (db) => {
    if (!hasTable(db, "measurement_breaks")) return null;
    const row = db
      .prepare<unknown[], Record<string, unknown>>(
        "SELECT MIN(break_date) AS d FROM measurement_breaks WHERE scope = ? AND kind = 'arm_retired'",
      )
      .get(TWIN_ARM);
    return str(row?.["d"]);
  });
  return outcome.status === "ok" ? outcome.value : null;
}

/** {session: registry arms live ran or tried}, from the live ledger's entry attempts (its `ic_trades.arm`
 *  is the constant `live`, the account's tag, so the strategy arm is only on the attempts). */
function liveArmsBySession(file: string): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const outcome = readOnlyDb(file, (db) => {
    if (!hasTable(db, "entry_attempts") || !hasColumn(db, "entry_attempts", "arm") || !hasColumn(db, "entry_attempts", "trade_date")) return out;
    for (const r of db
      .prepare<unknown[], Record<string, unknown>>("SELECT DISTINCT trade_date, arm FROM entry_attempts WHERE arm IS NOT NULL")
      .all()) {
      const day = str(r["trade_date"]);
      const arm = str(r["arm"]);
      if (day !== null && arm !== null) out.set(day, (out.get(day) ?? new Set<string>()).add(arm));
    }
    return out;
  });
  return outcome.status === "ok" ? outcome.value : out;
}

/**
 * The paper rows that mirror live, and the sessions with no mirror.
 *
 * Until `live-shadow` retired (`retiredOn`) its rows are the mirror, as they always were: it followed
 * live, which then ran one arm. From that date each session's mirror is the twin of the arm live ran
 * THAT session, so a switch of live's arm moves the comparison with it. A session whose arm has no twin
 * has no mirror: its live condors show as having none, and it is listed, never matched to another arm's
 * twin or to `live-shadow`. `byArm` is paper's rows by session, then by arm.
 */
export function mirrorRows(
  byArm: Map<string, Map<string, MeicLiveGapSide[]>>,
  liveArms: Map<string, Set<string>>,
  twins: Map<string, string>,
  retiredOn: string | null,
): { paper: Map<string, MeicLiveGapSide[]>; unmirrored: string[] } {
  const paper = new Map<string, MeicLiveGapSide[]>();
  for (const [session, arms] of byArm) {
    if (retiredOn === null || session < retiredOn) {
      const rows = arms.get(TWIN_ARM);
      if (rows !== undefined) paper.set(session, rows);
      continue;
    }
    const wanted = [...(liveArms.get(session) ?? [])].map((a) => twins.get(a)).filter((t): t is string => t !== undefined);
    const rows = wanted.flatMap((t) => arms.get(t) ?? []);
    if (rows.length > 0) paper.set(session, rows);
  }
  const unmirrored = [...liveArms]
    .filter(([session, arms]) => retiredOn !== null && session >= retiredOn && [...arms].some((a) => !twins.has(a)))
    .map(([session]) => session)
    .sort();
  return { paper, unmirrored };
}

/** The mirror arms' resolved rows from `since` on, by session then by arm; null when unreadable. */
function paperRowsByArm(file: string, arms: string[], since: string): Map<string, Map<string, MeicLiveGapSide[]>> | null {
  const outcome = readOnlyDb(file, (db) => {
    const armCol = findArmColumn(db, "ic_trades");
    const out = new Map<string, Map<string, MeicLiveGapSide[]>>();
    if (armCol === null) return out;
    const rows = db
      .prepare<unknown[], Record<string, unknown>>(
        `SELECT trade_date, entry_time, symbol, put_strike, call_strike, wing_width, net_credit, status,
                pnl, fees, ${armCol} AS arm
           FROM ic_trades
          WHERE ${armCol} IN (${arms.map(() => "?").join(", ")}) AND ${RESOLVED} AND trade_date >= ?
          ORDER BY trade_date, entry_time`,
      )
      .all(...arms, since);
    for (const r of rows) {
      const day = str(r["trade_date"]);
      const arm = str(r["arm"]);
      if (day === null || arm === null) continue;
      const bySession = out.get(day) ?? new Map<string, MeicLiveGapSide[]>();
      bySession.set(arm, [...(bySession.get(arm) ?? []), side(r)]);
      out.set(day, bySession);
    }
    return out;
  });
  return outcome.status === "ok" ? outcome.value : null;
}

/**
 * One session's pairs: a live condor with the paper condor on the same strikes first, then what is
 * left by entry order, then the unpaired either side. On 2026-10-15 two condors a side entered
 * minutes apart, and entry order alone paired each with the other's strikes.
 */
export function pairsOf(
  live: MeicLiveGapSide[],
  paper: MeicLiveGapSide[],
): Array<[MeicLiveGapSide | null, MeicLiveGapSide | null]> {
  const key = (s: MeicLiveGapSide) => `${s.symbol}|${s.putStrike}|${s.callStrike}`;
  const left = [...paper];
  const out: Array<[MeicLiveGapSide | null, MeicLiveGapSide | null]> = [];
  const rest: MeicLiveGapSide[] = [];
  for (const a of live) {
    const i = left.findIndex((b) => key(b) === key(a));
    if (i >= 0) out.push([a, left.splice(i, 1)[0] ?? null]);
    else rest.push(a);
  }
  for (const a of rest) out.push([a, left.shift() ?? null]);
  for (const b of left) out.push([null, b]);
  return out.sort((x, y) => String((x[0] ?? x[1])?.entry).localeCompare(String((y[0] ?? y[1])?.entry)));
}

const NO_TOTALS ={ paired: 0, liveNet: 0, paperNet: 0, gap: 0, paperOnly: 0, paperOnlyNet: 0, liveOnly: 0 };

export function readMeicLiveGap(config: ConsoleConfig): MeicLiveGapPayload {
  const dir = config.paths.meicDir;
  const liveFile = path.join(dir, "meic_trades.db");
  const paperFile = path.join(dir, "paper_trades.db");
  const live = rowsOf(liveFile, LIVE_ARM, null);
  const empty: MeicLiveGapPayload = { twin: TWIN_ARM, ledger: "absent", since: null, rows: [], totals: NO_TOTALS, unmirrored: [] };
  if (live === null) return empty;
  const since = [...live.keys()].sort()[0] ?? null;
  const twins = meicTwins(config);
  const retiredOn = shadowRetiredOn(paperFile);
  let paper = new Map<string, MeicLiveGapSide[]>();
  let unmirrored: string[] = [];
  if (since !== null) {
    const byArm = paperRowsByArm(paperFile, [TWIN_ARM, ...twins.values()], since);
    if (byArm === null) return empty;
    ({ paper, unmirrored } = mirrorRows(byArm, liveArmsBySession(liveFile), twins, retiredOn));
  }
  const twin = retiredOn === null ? TWIN_ARM : `${TWIN_ARM} before ${retiredOn}, then each session's arm twin`;

  const rows: MeicLiveGapRow[] = [];
  const totals = { ...NO_TOTALS };
  const sessions = [...new Set([...live.keys(), ...paper.keys()])].sort();
  for (const session of sessions) {
    for (const [a, b] of pairsOf(live.get(session) ?? [], paper.get(session) ?? [])) {
      const gap = a?.net != null && b?.net != null ? round2(a.net - b.net) : null;
      rows.push({ session, live: a, paper: b, gap });
      if (gap !== null && a?.net != null && b?.net != null) {
        totals.paired += 1;
        totals.liveNet += a.net;
        totals.paperNet += b.net;
      } else if (a === null && b !== null) {
        totals.paperOnly += 1;
        totals.paperOnlyNet += b.net ?? 0;
      } else if (b === null && a !== null) {
        totals.liveOnly += 1;
      }
    }
  }
  totals.liveNet = round2(totals.liveNet);
  totals.paperNet = round2(totals.paperNet);
  totals.gap = round2(totals.liveNet - totals.paperNet);
  totals.paperOnlyNet = round2(totals.paperOnlyNet);
  return { twin, ledger: "ok", since, rows: rows.reverse(), totals, unmirrored };
}
