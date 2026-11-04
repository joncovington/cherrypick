import fs from "node:fs";
import path from "node:path";
import type { LiveTodayHand, LiveTodayHandLeg, LiveTodayHandOrder } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { listSessions } from "./manage.js";

/**
 * The Live page's `today` tab's hand row (2026-11-03): today's 0DTE trades placed by hand, from
 * packages/manage's evaluation (`zero_dte`, `manage/zerodte.py`). manage leaves every 0DTE leg on its
 * excluded names out of its trades, the loops' and the hand's alike; its `zero_dte` block takes the
 * loops' fills away by the order ids each loop publishes for its orphan sweep, reconciles what is
 * left against the held quantities, and states the money. This reads that block and shapes it. It
 * splits nothing, reconciles nothing and sums nothing (a second copy of the split would be free to
 * disagree with the first).
 *
 * Only TODAY's evaluation counts: yesterday's hand trades are not today's. The newest re-mark (the
 * minute `manage-marks` job rewrites the evaluation) is what `markedAt` says.
 */

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function n(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function empty(status: LiveTodayHand["status"], reason: string | null): LiveTodayHand {
  return {
    status,
    reason,
    evaluatedSession: null,
    snapshotAt: null,
    markedAt: null,
    underlyings: [],
    orders: [],
    openLegs: [],
    unreconciled: [],
    expiredHeld: [],
    afterPublish: [],
    knownModules: {},
    loopFills: 0,
    cash: null,
    mark: null,
    gross: null,
    fees: null,
    settlement: null,
    net: null,
    netBasis: null,
    caveats: [],
  };
}

const KINDS = new Set(["open", "close", "mixed", "unknown"]);

function order(v: unknown): LiveTodayHandOrder {
  const o = rec(v);
  const kind = str(o["kind"]);
  return {
    orderId: str(o["order_id"]),
    at: str(o["at"]),
    kind: kind !== null && KINDS.has(kind) ? (kind as LiveTodayHandOrder["kind"]) : "unknown",
    legs: arr(o["legs"]).map((l) => {
      const x = rec(l);
      return { symbol: str(x["symbol"]) ?? "", action: str(x["action"]) ?? "", quantity: n(x["quantity"]), price: n(x["price"]), value: n(x["value"]) };
    }),
    cash: n(o["cash"]) ?? 0,
    fees: n(o["fees"]),
  };
}

function leg(v: unknown): LiveTodayHandLeg {
  const l = rec(v);
  return {
    symbol: str(l["symbol"]) ?? "",
    right: str(l["right"]),
    strike: n(l["strike"]),
    quantity: n(l["quantity"]) ?? 0,
    mid: n(l["mid"]),
    value: n(l["value"]),
    markSource: str(l["mark_source"]),
    markAgeSeconds: n(l["mark_age_seconds"]),
  };
}

export function readLiveHand(config: ConsoleConfig, session: string): LiveTodayHand {
  const sessions = listSessions(config);
  if (!sessions.includes(session)) {
    const last = sessions[sessions.length - 1];
    return empty("none", last === undefined ? "no position evaluation on this machine" : `no position evaluation today yet (newest ${last})`);
  }
  let doc: Rec;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(config.paths.manageDir, `eval-${session}.json`), "utf-8")));
  } catch {
    return empty("unread", `eval-${session}.json is unreadable`);
  }
  const base = {
    evaluatedSession: session,
    snapshotAt: str(doc["snapshot_generated_at"]),
    markedAt: str(rec(doc["remark"])["at"]),
  };
  // The view is on the designated account (or the only one); manage leaves it null on any other.
  const block = arr(doc["accounts"]).map((a) => rec(a)["zero_dte"]).find((z) => z !== null && z !== undefined);
  if (block === undefined) {
    const reason =
      arr(doc["accounts"]).length === 0
        ? "today's evaluation holds no account"
        : "today's evaluation has no 0DTE view (written before it existed, or the ruleset excludes no name)";
    return { ...empty("unread", reason), ...base };
  }
  const z = rec(block);
  if (z["read"] !== true) return { ...empty("unread", str(z["error"]) ?? "manage could not read today's fills"), ...base, underlyings: arr(z["underlyings"]).map(String) };
  return {
    status: "ok",
    reason: null,
    ...base,
    underlyings: arr(z["underlyings"]).map(String),
    orders: arr(z["orders"]).map(order),
    openLegs: arr(z["open_legs"]).map(leg),
    unreconciled: arr(z["unreconciled"]).map((u) => {
      const x = rec(u);
      return { symbol: str(x["symbol"]) ?? "", held: n(x["held"]) ?? 0, loopFills: n(x["loop_fills"]) ?? 0, handFills: n(x["hand_fills"]) ?? 0 };
    }),
    // Absent on an evaluation written before manage set expired legs aside: an empty list then.
    expiredHeld: arr(z["expired_held"]).map((e) => {
      const x = rec(e);
      return { symbol: str(x["symbol"]) ?? "", expiry: str(x["expiry"]), quantity: n(x["quantity"]) };
    }),
    afterPublish: arr(z["after_publish"]).map(String),
    knownModules: Object.fromEntries(Object.entries(rec(z["known_modules"])).map(([k, v]) => [k, str(v)])),
    loopFills: n(z["loop_fills"]) ?? 0,
    cash: n(z["cash"]),
    mark: n(z["mark"]),
    gross: n(z["gross"]),
    fees: n(z["fees"]),
    settlement: n(z["settlement"]),
    net: n(z["net"]),
    netBasis: str(z["net_basis"]),
    caveats: arr(z["caveats"]).map(String),
  };
}
