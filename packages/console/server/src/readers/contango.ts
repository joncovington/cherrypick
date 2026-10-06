import path from "node:path";
import type {
  ContangoArmParams,
  ContangoArmState,
  ContangoFill,
  ContangoParams,
  ContangoPayload,
  ContangoSessionRow,
  ContangoStint,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasTable, num, obj, readJson, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";

/**
 * contango's read layer (packages/contango). Paper only and structural, like curve: no live loop,
 * one store, no `mode`. The money is the module's own -- each stint row already adds up (entry +
 * exit + distributions = gross; gross - fees - slippage = net), so this reads it and never
 * re-derives it. The tear sheet (CAGR, drawdown, the expected path) is not here: it comes from the
 * module's own `contango metrics` through `services/navBridge.ts`.
 */

const DB_FILE = "paper_trades.db";

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.contangoDir, DB_FILE);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** The module's declared arms and knobs, resolved the way the module resolves them (deployed config
 *  first, then the repo's, then the shipped example), so a threshold on the page is the one it runs. */
export function loadContangoParams(config: ConsoleConfig): ContangoParams {
  let doc: Record<string, unknown> | null = null;
  for (const candidate of config.paths.contangoConfigCandidates) {
    doc = readJson(candidate);
    if (doc !== null) break;
  }
  const defaults = obj(doc?.["defaults"]);
  const registry = obj(doc?.["arms"] ?? doc?.["books"]);
  const d = (key: string, fallback: number) => num(defaults[key]) ?? fallback;
  const arms: ContangoArmParams[] = Object.entries(registry).map(([arm, raw]) => {
    const b = { ...defaults, ...obj(raw) };
    const enter = num(b["enter_below"]) ?? 0.97;
    return {
      arm,
      enabled: b["enabled"] !== false,
      enterBelow: enter,
      exitAtOrAbove: num(b["exit_at_or_above"]) ?? enter,
      riskSymbol: (str(b["risk_symbol"]) ?? "SVXY").toUpperCase(),
      cashSymbol: (str(b["cash_symbol"]) ?? "SHV").toUpperCase(),
      startingCapital: num(b["starting_capital"]) ?? 10_000,
    };
  });
  return {
    arms,
    decisionMinutesBeforeClose: d("decision_minutes_before_close", 10),
    decisionWindowMinutes: d("decision_window_minutes", 8),
    slippageFloorBps: d("slippage_floor_bps", 2),
    maxSpreadBps: d("max_spread_bps", 50),
  };
}

function sessionRow(r: Record<string, unknown>): ContangoSessionRow {
  return {
    tradeDate: str(r["trade_date"]) ?? "",
    arm: str(r["arm"]) ?? "",
    ratio: num(r["ratio"]),
    stateBefore: str(r["state_before"]),
    stateAfter: str(r["state_after"]),
    action: str(r["action"]),
    refusal: str(r["refusal"]),
    holdingSymbol: str(r["holding_symbol"]),
    shares: num(r["shares"]),
    mark: num(r["mark"]),
    cash: num(r["cash"]),
    nav: num(r["nav"]),
  };
}

function latestMarks(db: DatabaseHandle): Map<string, number> {
  const out = new Map<string, number>();
  if (!hasTable(db, "contango_marks")) return out;
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT m.symbol, m.mid FROM contango_marks m
        JOIN (SELECT symbol, MAX(trade_date) AS d FROM contango_marks GROUP BY symbol) last
          ON last.symbol = m.symbol AND last.d = m.trade_date`,
    )
    .all()) {
    const mid = num(r["mid"]);
    if (mid !== null) out.set(str(r["symbol"]) ?? "", mid);
  }
  return out;
}

function readStints(db: DatabaseHandle): ContangoStint[] {
  const marks = latestMarks(db);
  return db
    .prepare<[], Record<string, unknown>>("SELECT * FROM contango_positions ORDER BY entry_session DESC, id DESC")
    .all()
    .map((r) => {
      const status = str(r["status"]) ?? "";
      const symbol = str(r["symbol"]) ?? "";
      const shares = num(r["shares"]) ?? 0;
      const entryValue = num(r["entry_value"]);
      const mark = status === "open" ? (marks.get(symbol) ?? null) : null;
      const entryCosts = (num(r["entry_fees"]) ?? 0) + (num(r["entry_slippage"]) ?? 0);
      const distributions = num(r["distributions"]) ?? 0;
      return {
        positionId: str(r["position_id"]) ?? "",
        arm: str(r["arm"]) ?? "",
        symbol,
        role: str(r["role"]) ?? "",
        shares,
        status,
        entrySession: str(r["entry_session"]) ?? "",
        entryMid: num(r["entry_mid"]),
        entryValue,
        entryRatio: num(r["entry_ratio"]),
        exitSession: str(r["exit_session"]),
        exitMid: num(r["exit_mid"]),
        exitValue: num(r["exit_value"]),
        exitRatio: num(r["exit_ratio"]),
        exitReason: str(r["exit_reason"]),
        distributions,
        grossPnl: num(r["gross_pnl"]),
        fees: num(r["fees"]),
        slippage: num(r["slippage"]),
        netPnl: num(r["net_pnl"]),
        mark,
        // Marked at mid, less the entry's fee and slippage already spent, plus anything distributed.
        unrealisedNet:
          mark === null || entryValue === null ? null : round2(shares * mark + entryValue + distributions - entryCosts),
      };
    });
}

function fills(db: DatabaseHandle): ContangoFill[] {
  const out: ContangoFill[] = [];
  const bps = (slip: number | null, value: number | null) =>
    slip === null || value === null || value === 0 ? null : Math.round((slip / Math.abs(value)) * 1e4 * 100) / 100;
  for (const r of db
    .prepare<[], Record<string, unknown>>("SELECT * FROM contango_positions ORDER BY entry_session, id")
    .all()) {
    const base = { arm: str(r["arm"]) ?? "", symbol: str(r["symbol"]) ?? "", shares: num(r["shares"]) ?? 0 };
    const entryValue = num(r["entry_value"]);
    const entrySlip = num(r["entry_slippage"]);
    out.push({
      ...base,
      session: str(r["entry_session"]) ?? "",
      side: "buy",
      value: Math.abs(entryValue ?? 0),
      slippage: entrySlip ?? 0,
      slippageBps: bps(entrySlip, entryValue),
      fees: num(r["entry_fees"]) ?? 0,
    });
    if (str(r["status"]) === "closed") {
      const exitValue = num(r["exit_value"]);
      const exitSlip = num(r["exit_slippage"]);
      out.push({
        ...base,
        session: str(r["exit_session"]) ?? "",
        side: "sell",
        value: Math.abs(exitValue ?? 0),
        slippage: exitSlip ?? 0,
        slippageBps: bps(exitSlip, exitValue),
        fees: num(r["exit_fees"]) ?? 0,
      });
    }
  }
  return out.sort((a, b) => b.session.localeCompare(a.session));
}

function emptyPayload(params: ContangoParams): ContangoPayload {
  return {
    session: null,
    dbPresent: false,
    params,
    regimeSeries: [],
    arms: [],
    sessions: [],
    stints: [],
    fills: [],
    distributions: [],
    measurementBreaks: [],
    lastIteration: null,
  };
}

export function readContango(config: ConsoleConfig): ContangoPayload {
  const params = loadContangoParams(config);
  return withReadOnlyDb<ContangoPayload>(dbPath(config), emptyPayload(params), (db) => {
    const session =
      db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM contango_loop_iterations").get()?.d ??
      db.prepare<[], { d: string | null }>("SELECT MAX(trade_date) AS d FROM contango_sessions").get()?.d ??
      null;
    const sessions = db
      .prepare<[], Record<string, unknown>>("SELECT * FROM contango_sessions ORDER BY trade_date DESC, arm")
      .all()
      .map(sessionRow);
    const stints = readStints(db);
    const distributions = db
      .prepare<[], Record<string, unknown>>("SELECT * FROM contango_distributions ORDER BY ex_date DESC, arm")
      .all()
      .map((r) => ({
        arm: str(r["arm"]) ?? "",
        symbol: str(r["symbol"]) ?? "",
        exDate: str(r["ex_date"]) ?? "",
        perShare: num(r["per_share"]) ?? 0,
        shares: num(r["shares"]) ?? 0,
        amount: num(r["amount"]) ?? 0,
        creditedSession: str(r["credited_session"]) ?? "",
      }));
    const accounts = new Map(
      db
        .prepare<[], Record<string, unknown>>("SELECT * FROM contango_accounts")
        .all()
        .map((r) => [str(r["arm"]) ?? "", r] as const),
    );
    const armNames = [...new Set([...params.arms.map((a) => a.arm), ...accounts.keys()])];
    const arms: ContangoArmState[] = armNames.map((arm) => {
      const acct = accounts.get(arm);
      const open = stints.find((s) => s.arm === arm && s.status === "open");
      const mine = sessions.filter((s) => s.arm === arm);
      return {
        arm,
        startingCapital: acct === undefined ? null : num(acct["starting_capital"]),
        cash: acct === undefined ? null : num(acct["cash"]),
        openedSession: acct === undefined ? null : str(acct["opened_session"]),
        holding:
          open === undefined
            ? null
            : { symbol: open.symbol, role: open.role, shares: open.shares, entrySession: open.entrySession, entryMid: open.entryMid },
        today: mine.find((s) => s.tradeDate === session) ?? null,
        latestNav: mine.find((s) => s.nav !== null)?.nav ?? null,
        switches: mine.filter((s) => s.action === "switch").length,
        missed: mine.filter((s) => s.action === "missed").length,
        distributionsTotal: round2(distributions.filter((d) => d.arm === arm).reduce((t, d) => t + d.amount, 0)),
      };
    });
    const iteration = db
      .prepare<[], Record<string, unknown>>(
        "SELECT ran_at, phase, status FROM contango_loop_iterations ORDER BY ran_at DESC LIMIT 1",
      )
      .get();
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);
    return {
      session,
      dbPresent: true,
      params,
      regimeSeries: db
        .prepare<[], Record<string, unknown>>("SELECT * FROM contango_regime ORDER BY trade_date")
        .all()
        .map((r) => ({
          tradeDate: str(r["trade_date"]) ?? "",
          ratio: num(r["ratio"]),
          vix: num(r["vix"]),
          vix3m: num(r["vix3m"]),
          usable: r["usable"] === 1,
          refusal: str(r["refusal"]),
        })),
      arms,
      sessions,
      stints,
      fills: fills(db),
      distributions,
      measurementBreaks: db
        .prepare<[], Record<string, unknown>>("SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC")
        .all()
        .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "", note: str(r["note"]) })),
      lastIteration:
        iteration === undefined || ranAt === null
          ? null
          : {
              ranAt,
              phase: str(iteration["phase"]) ?? "",
              status: str(iteration["status"]) ?? "",
              ageSeconds: Math.max(0, Date.now() / 1000 - ranAt),
            },
    };
  });
}

/** The session every contango card names: the loop's last run. */
export function resolveContangoSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(dbPath(config), null, (db) => {
    return (
      db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM contango_loop_iterations").get()?.d ?? null
    );
  });
}
