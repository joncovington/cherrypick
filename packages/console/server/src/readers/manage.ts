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
  ManageBand,
  ManageCandidate,
  ManageCandidates,
  ManageChainOrder,
  ManageHedges,
  ManageLadder,
  ManageScenarios,
  ManageHedgeSuggestion,
  ManagePortfolio,
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
  const kinds = ["credit", "debit", "directional", "risk-judged"] as const;
  const kind = kinds.find((k) => k === t["kind"]) ?? null;
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
    earningsTrade: str(rec(t["earnings_trade"])["report"])
      ? {
          report: str(rec(t["earnings_trade"])["report"]) as string,
          timeOfDay: str(rec(t["earnings_trade"])["time_of_day"]),
          leadDays: num(rec(t["earnings_trade"])["lead_days"]),
          passed: rec(t["earnings_trade"])["passed"] === true,
        }
      : null,
    heldForEarnings: arr(t["held_for_earnings"]).map(fired),
  };
}

function band(v: unknown): ManageBand {
  const b = rec(v);
  return {
    low: num(b["low"]),
    high: num(b["high"]),
    value: num(b["value"]),
    inside: typeof b["inside"] === "boolean" ? b["inside"] : null,
  };
}

function hedges(v: unknown): ManageHedges | null {
  if (v === null || v === undefined) return null;
  const h = rec(v);
  return {
    target: num(h["target"]),
    need: num(h["need"]),
    root: str(h["root"]),
    expiration: str(h["expiration"]),
    dte: num(h["dte"]),
    error: str(h["error"]),
    suggestions: arr(h["suggestions"]).map((raw): ManageHedgeSuggestion => {
      const s = rec(raw);
      const after = rec(s["after"]);
      const be = rec(after["breakeven"]);
      return {
        kind: str(s["kind"]) ?? "?",
        structure: str(s["structure"]),
        error: str(s["error"]),
        units: num(s["units"]),
        legs: arr(s["legs"]).map((l) => {
          const leg = rec(l);
          return {
            label: str(leg["label"]) ?? "?",
            quantity: num(leg["quantity"]) ?? 0,
            bid: num(leg["bid"]),
            ask: num(leg["ask"]),
            mid: num(leg["mid"]),
            delta: num(leg["delta"]),
          };
        }),
        priceLabel: str(s["price_label"]),
        natural: num(s["natural"]),
        cash: num(s["cash"]),
        maxLoss: num(s["max_loss"]),
        bwdChange: num(s["bwd_change"]),
        thetaChange: num(s["theta_change"]),
        pastCap: s["past_cap"] === true,
        after: Object.keys(after).length
          ? {
              bwd: num(after["bwd"]),
              inBand: typeof after["in_band"] === "boolean" ? after["in_band"] : null,
              theta: num(after["theta"]),
              thetaPctNetLiq: num(after["theta_pct_net_liq"]),
              breakevenPct: num(be["pct"]),
              buyingPowerUsedPct: num(after["buying_power_used_pct"]),
            }
          : null,
      };
    }),
  };
}

/** The portfolio reading, passed through. Absent on an evaluation written before it existed. */
function portfolio(v: unknown): ManagePortfolio | null {
  const p = rec(v);
  if (Object.keys(p).length === 0) return null;
  const kind = rec(p["account_type"]);
  const be = rec(p["breakeven"]);
  const bands = rec(p["bands"]);
  return {
    benchmark: str(p["benchmark"]),
    benchmarkSpot: num(p["benchmark_spot"]),
    complete: p["complete"] === true,
    unmodelledLegs: strs(p["unmodelled_legs"]),
    noBeta: strs(p["no_beta"]),
    accountKind: { marginOrCash: str(kind["margin_or_cash"]), typeName: str(kind["type_name"]) },
    netLiq: num(p["net_liq"]),
    bwd: num(p["bwd"]),
    bwdDollarsPer1pct: num(p["bwd_dollars_per_1pct"]),
    theta: num(p["theta"]),
    thetaPctNetLiq: num(p["theta_pct_net_liq"]),
    vega30: num(p["vega_30"]),
    breakeven:
      be["kind"] === "short_gamma" || be["kind"] === "long_gamma"
        ? { kind: be["kind"], points: num(be["points"]), pct: num(be["pct"]) }
        : null,
    expectedMovePct: num(p["expected_move_pct"]),
    expectedMove: num(p["expected_move"]),
    realisedMove: num(p["realised_move"]),
    buyingPowerUsed: num(p["buying_power_used"]),
    buyingPowerUsedPct: num(p["buying_power_used_pct"]),
    stress: arr(p["stress"]).map((raw) => {
      const s = rec(raw);
      return { shock: num(s["shock"]) ?? 0, pnl: num(s["pnl"]), pctNetLiq: num(s["pct_net_liq"]), leftOut: strs(s["left_out"]) };
    }),
    bands: Object.keys(bands).length
      ? {
          delta: { ...band(bands["delta"]), basis: str(rec(bands["delta"])["basis"]) },
          theta: band(bands["theta"]),
          buyingPower: bands["buying_power"]
            ? { ...band(bands["buying_power"]), vix: num(rec(bands["buying_power"])["vix"]) }
            : null,
        }
      : null,
    guidance: arr(p["guidance"]).map((raw) => {
      const g = rec(raw);
      return {
        rule: str(g["rule"]) ?? "?",
        confidence: num(g["confidence"]),
        reason: str(g["reason"]) ?? "",
        drivers: arr(g["drivers"]).map((d) => {
          const row = rec(d);
          const figure = Object.entries(row).find(([k]) => k !== "underlying");
          return { underlying: str(row["underlying"]) ?? "?", value: figure ? num(figure[1]) : null };
        }),
      };
    }),
    underlyings: arr(p["underlyings"]).map((raw) => {
      const u = rec(raw);
      return {
        underlying: str(u["underlying"]) ?? "?",
        beta: num(u["beta"]),
        bwd: num(u["bwd"]),
        theta: num(u["theta"]),
        vega30: num(u["vega_30"]),
        bwdShare: num(u["bwd_share"]),
        thetaShare: num(u["theta_share"]),
        betaDoubt: str(u["beta_doubt"]),
      };
    }),
    hedges: hedges(p["hedges"]),
    vix: num(p["vix"]),
    scenarios: scenarios(p["scenarios"]),
    ladder: ladder(p["ladder"]),
    candidates: candidates(p["candidates"]),
  };
}

function candidate(v: unknown): ManageCandidate {
  const c = rec(v);
  const pu = rec(c["per_unit"]);
  return {
    underlying: str(c["underlying"]) ?? "?",
    kind: str(c["kind"]) ?? "?",
    alternativeTo: str(c["alternative_to"]),
    expiry: str(c["expiry"]),
    dte: num(c["dte"]),
    legs: arr(c["legs"]).map((raw) => {
      const l = rec(raw);
      return {
        strike: num(l["strike"]) ?? 0,
        right: str(l["right"]) ?? "?",
        quantity: num(l["quantity"]) ?? 0,
        bid: num(l["bid"]),
        ask: num(l["ask"]),
        delta: num(l["delta"]),
      };
    }),
    units: num(c["units"]) ?? 0,
    binding: str(c["binding"]),
    perUnit: {
      credit: num(pu["credit"]),
      natural: num(pu["natural"]),
      cash: num(pu["cash"]),
      maxLoss: num(pu["max_loss"]),
      buyingPower: num(pu["buying_power"]),
      stressLoss: num(pu["stress_loss"]),
      theta: num(pu["theta"]),
      bwd: num(pu["bwd"]),
    },
    thetaPerBp: num(c["theta_per_bp"]),
    earningsDate: str(c["earnings_date"]),
    earningsUnknown: c["earnings_unknown"] === true,
    alternative: c["alternative"] ? candidate(c["alternative"]) : null,
  };
}

function candidates(v: unknown): ManageCandidates | null {
  if (v === null || v === undefined) return null;
  const c = rec(v);
  const excluded: Record<string, number> = {};
  for (const [k, n] of Object.entries(rec(c["excluded"]))) {
    const x = num(n);
    if (x !== null) excluded[k] = x;
  }
  return {
    sourceSession: str(c["source_session"]),
    takenAt: str(c["taken_at"]),
    accountBasis: str(c["account_basis"]),
    names: num(c["names"]),
    excluded,
    fits: arr(c["fits"]).map(candidate),
    fitsTotal: num(c["fits_total"]) ?? 0,
    tooBig: arr(c["too_big"]).map(candidate),
    tooBigTotal: num(c["too_big_total"]) ?? 0,
    error: str(c["error"]),
  };
}

function nums(v: unknown): Array<number | null> {
  return arr(v).map((x) => num(x));
}

function scenarios(v: unknown): ManageScenarios | null {
  const s = rec(v);
  if (Object.keys(s).length === 0) return null;
  const graph = rec(s["graph"]);
  return {
    moves: nums(s["moves"]).filter((x): x is number => x !== null),
    ivShiftWeighting: str(s["iv_shift_weighting"]),
    matrix: arr(s["matrix"]).map((raw) => {
      const row = rec(raw);
      return { ivShift: num(row["iv_shift"]) ?? 0, pnl: nums(row["pnl"]) };
    }),
    graph: {
      moves: nums(graph["moves"]).filter((x): x is number => x !== null),
      curves: arr(graph["curves"]).map((raw) => {
        const c = rec(raw);
        return { horizonDays: num(c["horizon_days"]) ?? 0, pnl: nums(c["pnl"]) };
      }),
    },
    leftOut: strs(s["left_out"]),
  };
}

function ladder(v: unknown): ManageLadder | null {
  const l = rec(v);
  if (Object.keys(l).length === 0) return null;
  const e = rec(l["earnings"]);
  return {
    expiries: arr(l["expiries"]).map((raw) => {
      const x = rec(raw);
      return {
        expiration: str(x["expiration"]) ?? "?",
        trades: strs(x["trades"]),
        downside: num(x["downside"]),
        downsideUnknown: num(x["downside_unknown"]) ?? 0,
        theta: num(x["theta"]),
      };
    }),
    earnings: { trades: strs(e["trades"]), theta: num(e["theta"]), thetaShare: num(e["theta_share"]), downside: num(e["downside"]) },
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
    portfolio: portfolio(a["portfolio"]),
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
