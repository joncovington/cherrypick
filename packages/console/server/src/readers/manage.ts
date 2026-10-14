/**
 * The daily position-management evaluation: read packages/manage's `eval-<session>.json`, shape it
 * for the page, derive nothing. The verdicts, exit levels and confidences are the package's; a
 * console that recomputed any of them would be a second ruleset waiting to disagree with the first.
 *
 * Fields are read defensively -- the artifact is versioned and will gain fields -- and an absent
 * number stays null, never zero.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  ManageAccount,
  ManageChainOrder,
  ManageAction,
  ManageFired,
  ManageLeg,
  ManageLevel,
  ManageOrder,
  ManagePayload,
  ManageTrade,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num } from "./db.js";

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function strs(v: unknown): string[] {
  return arr(v).filter((x): x is string => typeof x === "string");
}

const ACTIONS: readonly ManageAction[] = [
  "ROLL_FOR_CREDIT",
  "CLOSE",
  "TAKE_PROFIT",
  "ROLL_OR_CLOSE",
  "CLOSE_LEGS",
  "REVIEW",
  "SETTLES",
  "HOLD",
];

function action(v: unknown): ManageAction {
  return ACTIONS.includes(v as ManageAction) ? (v as ManageAction) : "REVIEW";
}

function side(v: unknown): "cr" | "db" | null {
  return v === "cr" || v === "db" ? v : null;
}

export function listSessions(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(config.paths.manageDir)
      .filter((f) => f.startsWith("eval-") && f.endsWith(".json"))
      .map((f) => f.slice(5, -5))
      .sort();
  } catch {
    return [];
  }
}

function level(v: unknown): ManageLevel | null {
  const l = rec(v);
  if (Object.keys(l).length === 0) return null;
  return {
    closeValue: num(l["close_value"]),
    netPrice: num(l["net_price"]),
    netSide: side(l["net_side"]),
    basis: str(l["basis"]),
  };
}

function order(v: unknown): ManageOrder | null {
  const o = rec(v);
  if (Object.keys(o).length === 0) return null;
  return {
    legs: arr(o["legs"]).map((raw) => {
      const r = rec(raw);
      return { side: str(r["side"]) ?? "?", quantity: num(r["quantity"]) ?? 0, leg: str(r["leg"]) ?? "?", mid: num(r["mid"]) };
    }),
    net: num(o["net"]),
    netPrice: num(o["net_price"]),
    netSide: side(o["net_side"]),
  };
}

function opening(v: unknown): ManageTrade["opening"] {
  const o = rec(v);
  if (Object.keys(o).length === 0) return null;
  return { net: num(o["net"]), netPrice: num(o["net_price"]), netSide: side(o["net_side"]) };
}

function chain(v: unknown): ManageTrade["chain"] {
  const c = rec(v);
  if (Object.keys(c).length === 0) return null;
  const orders: ManageChainOrder[] = arr(c["orders"]).map((raw) => {
    const o = rec(raw);
    return {
      date: str(o["date"]) ?? "?",
      role: str(o["role"]) ?? "?",
      net: num(o["net"]),
      netPrice: num(o["net_price"]),
      netSide: side(o["net_side"]),
      fees: num(o["fees"]),
      legs: strs(o["legs"]),
    };
  });
  return { orders, cash: num(c["cash"]), fees: num(c["fees"]), rolls: num(c["rolls"]) ?? 0 };
}

function roll(v: unknown): ManageTrade["roll"] {
  const r = rec(v);
  if (Object.keys(r).length === 0) return null;
  return {
    expiration: str(r["expiration"]) ?? "?",
    strike: num(r["strike"]) ?? 0,
    credit: num(r["credit"]),
    net: num(r["net"]),
    legs: strs(r["legs"]),
  };
}

function leg(v: unknown): ManageLeg {
  const l = rec(v);
  return {
    label: str(l["label"]) ?? str(l["symbol"]) ?? "?",
    quantity: num(l["quantity"]) ?? 0,
    expiration: str(l["expiration"]),
    dte: num(l["dte"]),
    openPrice: num(l["open_price"]),
    mid: num(l["mid"]),
    capture: num(l["capture"]),
    delta: num(l["delta"]),
    iv: num(l["iv"]),
  };
}

function fired(v: unknown): ManageFired {
  const f = rec(v);
  return {
    rule: str(f["rule"]) ?? "?",
    action: action(f["action"]),
    confidence: num(f["confidence"]),
    reason: str(f["reason"]) ?? "",
    legs: Array.isArray(f["legs"]) ? strs(f["legs"]) : null,
  };
}

function trade(v: unknown): ManageTrade {
  const t = rec(v);
  const r = rec(t["recommendation"]);
  const levels = rec(t["exit_levels"]);
  const er = rec(t["earnings"]);
  const xd = rec(t["ex_dividend"]);
  const pf = rec(t["profile"]);
  const kind = t["kind"] === "credit" || t["kind"] === "debit" || t["kind"] === "directional" ? t["kind"] : null;
  return {
    tradeId: str(t["trade_id"]) ?? str(t["underlying"]) ?? "?",
    underlying: str(t["underlying"]) ?? "?",
    spot: num(t["spot"]),
    kind,
    shape: str(t["shape"]),
    intent: str(t["intent"]),
    note: str(t["note"]),
    cost: num(t["cost"]),
    value: num(t["value"]),
    pnlGross: num(t["pnl_gross"]),
    pnlNet: num(t["pnl_net"]),
    fees: num(t["fees"]),
    basis: t["basis"] === "order_chain" || t["basis"] === "open_legs" ? t["basis"] : null,
    chain: chain(t["chain"]),
    capture: num(t["capture"]),
    nearestShortExpiry: str(t["nearest_short_expiry"]),
    nearestShortDte: num(t["nearest_short_dte"]),
    action: action(r["action"]),
    confidence: num(r["confidence"]),
    rule: str(r["rule"]),
    reason: str(r["reason"]) ?? "",
    explanation: str(t["explanation"]),
    agreeing: strs(r["agreeing"]),
    conflicts: strs(r["conflicts"]),
    replaces: strs(r["replaces"]),
    roll: roll(t["roll"]),
    fired: arr(t["fired"]).map(fired),
    legs: arr(t["legs"]).map(leg),
    opening: opening(t["opening"]),
    order: order(t["order"]),
    takeProfit: level(levels["take_profit"]),
    stop: level(levels["stop"]),
    timeExit: str(rec(levels["time_exit"])["date"]),
    earnings: str(er["date"])
      ? {
          date: str(er["date"]) as string,
          days: num(er["days"]) ?? 0,
          timeOfDay: str(er["time_of_day"]),
          beforeNearestShortExpiry: er["before_nearest_short_expiry"] === true,
        }
      : null,
    exDividend: str(xd["date"]) ? { date: str(xd["date"]) as string, days: num(xd["days"]) ?? 0 } : null,
    profile:
      Object.keys(pf).length === 0
        ? null
        : {
            horizon: str(pf["horizon"]),
            maxLossFromOpen: num(pf["max_loss_from_open"]),
            downsideFromNow: num(pf["downside_from_now"]),
            upsideFromNow: num(pf["upside_from_now"]),
            unboundedLow: pf["unbounded_low"] === true,
          },
  };
}

function account(v: unknown): ManageAccount {
  const a = rec(v);
  const counts: Partial<Record<ManageAction, number>> = {};
  for (const [k, n] of Object.entries(rec(a["counts"]))) {
    const c = num(n);
    if (c !== null && ACTIONS.includes(k as ManageAction)) counts[k as ManageAction] = c;
  }
  return {
    account: str(a["account"]),
    netLiq: num(a["net_liq"]),
    maintenance: num(a["maintenance"]),
    buyingPower: num(a["buying_power"]),
    riskBudget: num(a["risk_budget"]),
    pnlGross: num(a["pnl_gross"]),
    counts,
    trades: arr(a["trades"]).map(trade),
    excluded: arr(a["excluded"]).map((raw) => {
      const e = rec(raw);
      return { underlying: str(e["underlying"]) ?? "?", legs: num(e["legs"]) ?? 0, reason: str(e["reason"]) ?? "" };
    }),
  };
}

export function readManage(config: ConsoleConfig, session?: string): ManagePayload {
  const sessions = listSessions(config);
  const wanted = session && sessions.includes(session) ? session : sessions[sessions.length - 1];
  const empty: ManagePayload = {
    session: null,
    sessions,
    snapshotGeneratedAt: null,
    ok: false,
    error: null,
    metricsError: null,
    transactionsError: null,
    ruleset: null,
    rulesSource: null,
    ruleWarnings: [],
    rules: null,
    accounts: [],
  };
  if (!wanted) return empty;
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(config.paths.manageDir, `eval-${wanted}.json`), "utf-8")));
  } catch {
    return { ...empty, session: wanted, error: `eval-${wanted}.json is unreadable` };
  }
  return {
    session: wanted,
    sessions,
    snapshotGeneratedAt: str(doc["snapshot_generated_at"]),
    ok: doc["ok"] === true,
    error: str(doc["error"]),
    metricsError: str(doc["metrics_error"]),
    transactionsError: str(doc["transactions_error"]),
    ruleset: str(doc["ruleset"]),
    rulesSource: str(doc["rules_source"]),
    ruleWarnings: strs(doc["rule_warnings"]),
    rules: Object.keys(rec(doc["rules"])).length ? rec(doc["rules"]) : null,
    accounts: arr(doc["accounts"]).map(account),
  };
}
