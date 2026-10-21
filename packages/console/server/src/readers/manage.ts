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
  ManageBpLevel,
  ManageBrokerCheck,
  ManageCandidate,
  ManageCandidateRoom,
  ManageCandidates,
  ManageChainOrder,
  ManageHedges,
  ManageLadder,
  ManageScenarios,
  ManageHedgeSuggestion,
  ManagePortfolio,
  ManageAction,
  ManageFired,
  ManageDriverKind,
  ManageGuidance,
  ManageLeg,
  ManageLevel,
  ManageOrder,
  ManagePayload,
  ManageTrade,
  ManageWheel,
  ManageWheelCheck,
  ManageWheelCycle,
  ManageWheelNextLeg,
  ManageWheelNote,
  ManageWheelQuote,
  ManageWheelRoll,
  ManageWheelRolls,
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
          days: num(er["days"]),
          timeOfDay: str(er["time_of_day"]),
          beforeNearestShortExpiry: er["before_nearest_short_expiry"] === true,
        }
      : null,
    exDividend: str(xd["date"]) ? { date: str(xd["date"]) as string, days: num(xd["days"]) } : null,
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
    wheel: Array.isArray(t["wheel"]) ? t["wheel"].map(wheelNote) : null,
  };
}

/* ------------------------------------------------------------------ the wheel view (wheel.py) */

/** A boolean flag manage set, or null where it set none: never a guessed false. */
function flag(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

/** An id the broker may write as a number or a string. */
function ident(v: unknown): string | null {
  return typeof v === "number" && Number.isFinite(v) ? String(v) : str(v);
}

function quote(v: unknown): ManageWheelQuote | null {
  if (v === null || v === undefined) return null;
  const q = rec(v);
  return { strike: num(q["strike"]), bid: num(q["bid"]), ask: num(q["ask"]), mid: num(q["mid"]) };
}

function wheelRoll(v: unknown): ManageWheelRoll | null {
  if (v === null || v === undefined) return null;
  const r = rec(v);
  const c = r["check"] === null || r["check"] === undefined ? null : rec(r["check"]);
  return {
    closePrice: num(r["close_price"]),
    open: quote(r["open"]),
    net: num(r["net"]),
    expiration: str(r["expiration"]),
    legs: strs(r["legs"]),
    check: c
      ? {
          verdict: str(c["verdict"]),
          credit: num(c["credit"]),
          maxLossBefore: num(c["max_loss_before"]),
          maxLossAfter: num(c["max_loss_after"]),
          shapeBefore: str(c["shape_before"]),
          shapeAfter: str(c["shape_after"]),
          problems: strs(c["problems"]),
          notes: strs(c["notes"]),
          after: strs(c["after"]),
        }
      : null,
    checkError: str(r["check_error"]),
  };
}

function wheelRolls(v: unknown): ManageWheelRolls {
  const r = rec(v);
  return {
    leg: str(r["leg"]) ?? "?",
    expiration: str(r["expiration"]),
    quotedAt: str(r["quoted_at"]),
    why: str(r["why"]),
    netZero: wheelRoll(r["net_zero"]),
    netZeroWhy: str(r["net_zero_why"]),
    premiumGambit: wheelRoll(r["premium_gambit"]),
    premiumGambitWhy: str(r["premium_gambit_why"]),
  };
}

function nextLeg(v: unknown): ManageWheelNextLeg | null {
  if (v === null || v === undefined) return null;
  const n = rec(v);
  return {
    strike: num(n["strike"]),
    band: num(n["band"]),
    bandKind: str(n["band_kind"]),
    expiration: str(n["expiration"]),
    why: str(n["why"]),
    quote: quote(n["quote"]),
    contracts: num(n["contracts"]),
    belowWheelBasis: flag(n["below_wheel_basis"]),
  };
}

function wheelNote(v: unknown): ManageWheelNote {
  const n = rec(v);
  return {
    underlying: str(n["underlying"]) ?? "?",
    start: str(n["start"]),
    status: str(n["status"]),
    problems: Array.isArray(n["problems"]) ? strs(n["problems"]) : null,
    takeProfit: Array.isArray(n["take_profit"])
      ? n["take_profit"].map((raw) => {
          const t = rec(raw);
          return { leg: str(t["leg"]) ?? "?", mid: num(t["mid"]), btcPrice: num(t["btc_price"]), source: str(t["source"]), hit: flag(t["hit"]) };
        })
      : null,
    rolls: Array.isArray(n["rolls"]) ? n["rolls"].map(wheelRolls) : null,
    nextLeg: nextLeg(n["next_leg"]),
  };
}

function wheelCheck(v: unknown): ManageWheelCheck {
  const c = rec(v);
  const band = c["band"] === null || c["band"] === undefined ? null : rec(c["band"]);
  const beyond = c["beyond_band"] === null || c["beyond_band"] === undefined ? null : rec(c["beyond_band"]);
  return {
    leg: str(c["leg"]) ?? str(c["symbol"]) ?? "?",
    symbol: str(c["symbol"]) ?? "?",
    right: str(c["right"]),
    strike: num(c["strike"]),
    expiration: str(c["expiration"]),
    dte: num(c["dte"]),
    quantity: num(c["quantity"]),
    spot: num(c["spot"]),
    mid: num(c["mid"]),
    openPrice: num(c["open_price"]),
    wide: flag(c["wide"]),
    tested: flag(c["tested"]),
    intrinsic: num(c["intrinsic"]),
    extrinsic: num(c["extrinsic"]),
    extrinsicDollars: num(c["extrinsic_dollars"]),
    band: band ? { day: str(band["day"]), shift: num(band["shift"]), fixed: num(band["fixed"]), byExpiry: num(band["by_expiry"]) } : null,
    bandMissing: str(c["band_missing"]),
    beyondBand: beyond ? { fixed: flag(beyond["fixed"]), byExpiry: flag(beyond["by_expiry"]) } : null,
    restingBtc: arr(c["resting_btc"]).map((raw) => {
      const r = rec(raw);
      return {
        orderId: ident(r["order_id"]),
        status: str(r["status"]),
        price: num(r["price"]),
        quantity: num(r["quantity"]),
        timeInForce: str(r["time_in_force"]),
        legs: num(r["legs"]),
      };
    }),
    btcPrice: num(c["btc_price"]),
    markAtOrUnderBtc: flag(c["mark_at_or_under_btc"]),
  };
}

function pnlPair(v: unknown): { realised: number | null; unrealised: number | null } {
  const p = rec(v);
  return { realised: num(p["realised"]), unrealised: num(p["unrealised"]) };
}

function wheelCycle(v: unknown): ManageWheelCycle {
  const c = rec(v);
  const be = c["breakeven"] === null || c["breakeven"] === undefined ? null : rec(c["breakeven"]);
  const y = c["skate_yield"] === null || c["skate_yield"] === undefined ? null : rec(c["skate_yield"]);
  return {
    underlying: str(c["underlying"]) ?? "?",
    status: str(c["status"]) ?? "?",
    reconciled: flag(c["reconciled"]),
    problems: strs(c["problems"]),
    flat: flag(c["flat"]),
    start: str(c["start"]),
    startKnown: flag(c["start_known"]),
    end: str(c["end"]),
    days: num(c["days"]),
    assignments: num(c["assignments"]),
    calledAway: num(c["called_away"]),
    shares: num(c["shares"]),
    basisKnown: flag(c["basis_known"]),
    lots: arr(c["lots"]).map((raw) => {
      const l = rec(raw);
      return { date: str(l["date"]), shares: num(l["shares"]), price: num(l["price"]), source: str(l["source"]) };
    }),
    openOptions: arr(c["open_options"]).map((raw) => {
      const o = rec(raw);
      return { symbol: str(o["symbol"]) ?? "?", leg: str(o["leg"]) ?? str(o["symbol"]) ?? "?", quantity: num(o["quantity"]) };
    }),
    credits: num(c["credits"]),
    debits: num(c["debits"]),
    netCash: num(c["net_cash"]),
    fees: num(c["fees"]),
    netPremium: num(c["net_premium"]),
    dividends: num(c["dividends"]),
    taxBasis: num(c["tax_basis"]),
    wheelBasis: num(c["wheel_basis"]),
    breakeven: be ? { price: num(be["price"]), basis: str(be["basis"]) } : null,
    optionPnl: pnlPair(c["option_pnl"]),
    stockPnl: pnlPair(c["stock_pnl"]),
    pnlNet: num(c["pnl_net"]),
    skateYield: y ? { value: num(y["value"]), capped: flag(y["capped"]), capital: num(y["capital"]), basis: str(y["basis"]) } : null,
    events: arr(c["events"]).map((raw) => {
      const e = rec(raw);
      return { date: str(e["date"]), what: str(e["what"]) ?? "?", legs: strs(e["legs"]), cash: num(e["cash"]), fees: num(e["fees"]) };
    }),
    today: arr(c["today"]).map(wheelCheck),
    tradeIds: strs(c["trade_ids"]),
  };
}

/** The account's wheel view, passed through. Null when the evaluation carries no `wheel` key. */
function wheel(v: unknown): ManageWheel | null {
  if (v === null || v === undefined) return null;
  const w = rec(v);
  const outside: Record<string, string[]> = {};
  for (const [k, rows] of Object.entries(rec(w["outside"]))) outside[k] = strs(rows);
  return {
    cycles: arr(w["cycles"]).map(wheelCycle),
    flagged: arr(w["flagged"]).map((raw) => {
      const f = rec(raw);
      return { underlying: str(f["underlying"]) ?? "?", date: str(f["date"]), kind: str(f["kind"]) ?? "?", row: str(f["row"]) };
    }),
    outside,
    bandsDay: str(w["bands_day"]),
    bandsMissing: str(w["bands_missing"]),
    error: str(w["error"]),
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

/**
 * Which figures each guidance rule's drivers carry (packages/manage `portfolio.guidance`), chosen per
 * rule so a figure is never stated in another's unit: theta drivers carry `theta` ($/day), delta
 * drivers `bwd` (benchmark shares), and concentration drivers BOTH shares, since the rule fires on
 * whichever is larger and taking the first key once showed the smaller theta share as "+0.3".
 */
const DRIVER_FIGURES: Record<string, readonly ManageDriverKind[]> = {
  "portfolio.theta": ["theta"],
  "portfolio.delta": ["bwd"],
  "portfolio.concentration": ["bwd_share", "theta_share"],
};
const DRIVER_KINDS: readonly ManageDriverKind[] = ["bwd", "theta", "bwd_share", "theta_share"];

/** One driver row with its figures by kind. A rule this reader does not know keeps every figure it
 *  recognises; a key it does not know is dropped rather than shown in a guessed unit. */
function drivers(rule: string | null, row: Record<string, unknown>): ManageGuidance["drivers"][number] {
  const kinds = DRIVER_FIGURES[rule ?? ""] ?? DRIVER_KINDS.filter((k) => k in row);
  return { underlying: str(row["underlying"]) ?? "?", figures: kinds.map((kind) => ({ kind, value: num(row[kind]) })) };
}

const PORTFOLIO_RULES = ["theta", "delta", "breakeven", "buying_power", "concentration"] as const;

/** The range of confidences the evaluation's own ruleset gives the portfolio rules; null when the
 *  evaluation embeds no ruleset (the caption then names no number rather than a stale one). */
function ruleConfidence(rules: Record<string, unknown>): ManagePortfolio["ruleConfidence"] {
  const cfg = rec(rules["portfolio"]);
  const all = PORTFOLIO_RULES.map((r) => num(rec(cfg[r])["confidence"])).filter((c): c is number => c !== null);
  return all.length ? { low: Math.min(...all), high: Math.max(...all) } : null;
}

/** The candidates' sizing caps from the evaluation's ruleset. The notional cap follows
 *  `candidates.size`: the cash figure for a cash basis, the margin figure for any other. */
function candidateCaps(rules: Record<string, unknown>, basis: string | null): ManageCandidates["caps"] {
  const cfg = rec(rec(rules["portfolio"])["candidates"]);
  if (Object.keys(cfg).length === 0) return null;
  return {
    perTradeRiskPct: num(cfg["per_trade_risk_pct"]),
    notionalPerNamePct: num(rec(cfg["notional_per_name_pct"])[basis === "cash" ? "cash" : "margin"]),
    maxNameThetaShare: num(cfg["max_name_theta_share"]),
  };
}

/** The portfolio reading, passed through. Absent on an evaluation written before it existed. */
function portfolio(v: unknown, rules: Record<string, unknown>): ManagePortfolio | null {
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
        drivers: arr(g["drivers"]).map((d) => drivers(str(g["rule"]), rec(d))),
      };
    }),
    ruleConfidence: ruleConfidence(rules),
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
    candidates: candidates(p["candidates"], rules),
    brokerCheck: brokerCheck(p["broker_check"]),
  };
}

/** The check against the broker's greeks, passed through; null where the snapshot carried none. */
function brokerCheck(v: unknown): ManageBrokerCheck | null {
  const c = rec(v);
  if (c["available"] !== true) return null;
  return {
    legsCompared: num(c["legs_compared"]) ?? 0,
    legsLeftOut: strs(c["legs_left_out"]),
    bwdOurs: num(c["bwd_ours"]),
    bwdBroker: num(c["bwd_broker"]),
    bwdGap: num(c["bwd_gap"]),
    thetaOurs: num(c["theta_ours"]),
    thetaBroker: num(c["theta_broker"]),
    thetaGap: num(c["theta_gap"]),
    underlyings: arr(c["underlyings"]).map((raw) => {
      const u = rec(raw);
      return {
        underlying: str(u["underlying"]) ?? "?",
        legs: num(u["legs"]) ?? 0,
        bwdGap: num(u["bwd_gap"]),
        thetaGap: num(u["theta_gap"]),
      };
    }),
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
    ifRoom: c["if_room"] ? candidateRoom(c["if_room"]) : null,
  };
}

const BP_LEVELS: ReadonlySet<string> = new Set(["ok", "over", "far_over"]);

function candidateRoom(v: unknown): ManageCandidateRoom {
  const r = rec(v);
  const level = str(r["bp_level"]);
  return {
    units: num(r["units"]) ?? 0,
    binding: str(r["binding"]),
    bpAfterPct: num(r["bp_after_pct"]),
    bpCapPct: num(r["bp_cap_pct"]),
    bpLevel: level !== null && BP_LEVELS.has(level) ? (level as ManageBpLevel) : null,
    thetaAfterPct: num(r["theta_after_pct"]),
    thetaCeilingPct: num(r["theta_ceiling_pct"]),
    thetaOver: r["theta_over"] === true,
  };
}

function candidates(v: unknown, rules: Record<string, unknown>): ManageCandidates | null {
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
    waiting: arr(c["waiting"]).map(candidate),
    waitingTotal: num(c["waiting_total"]) ?? 0,
    bpUsedPct: num(c["bp_used_pct"]),
    bpCapPct: num(c["bp_cap_pct"]),
    bpWarnOver: num(c["bp_warn_over"]),
    error: str(c["error"]),
    caps: candidateCaps(rules, str(c["account_basis"])),
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

function account(v: unknown, rules: Record<string, unknown>): ManageAccount {
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
    portfolio: portfolio(a["portfolio"], rules),
    wheel: wheel(a["wheel"]),
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
    accounts: arr(doc["accounts"]).map((a) => account(a, rec(doc["rules"]))),
  };
}
