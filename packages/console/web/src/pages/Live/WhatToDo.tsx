import { Link } from "react-router-dom";
import type { LiveTodayPayload, ManagePayload, ManageTrade } from "@console/shared";
import { ACTION_PRIORITY, earningsInLife } from "./ManagePage";

/**
 * What to do today (2026-10-30, docs/console-review-plan.md batch 5): one list from reads Live
 * already makes, so the actions stop being spread over four pages. In order of how soon they bite:
 * a live loop that needs a person; a position the manage evaluation says to act on (its own action and
 * reason, in its own urgency order -- nothing is re-judged here); a held position whose short expires
 * within a week or whose underlying reports before it does; and the income trades that fit the
 * account's room. Advice only: nothing here places or changes an order.
 */

export type TodoGroup = "person" | "act" | "expiring" | "earnings" | "income";

export interface TodoItem {
  group: TodoGroup;
  what: string;
  detail: string;
  to: string;
}

/** A held position whose nearest short is this close is listed as expiring. */
export const EXPIRING_DAYS = 7;

const QUIET = new Set(["HOLD", "SETTLES"]);

const words = (s: string) => s.replace(/_/g, " ").toLowerCase();

export function todoList(live: LiveTodayPayload | undefined, manage: ManagePayload | undefined): TodoItem[] {
  const out: TodoItem[] = [];
  for (const a of live?.attention ?? []) {
    out.push({ group: "person", what: `${a.loop}: ${words(a.kind)}`, detail: a.detail, to: a.to });
  }
  const accounts = manage?.accounts ?? [];
  const several = accounts.length > 1;
  for (const acct of accounts) {
    const who = several && acct.account ? ` (${acct.account})` : "";
    const acting = acct.trades
      .filter((t) => !QUIET.has(t.action))
      .sort((x, y) => (ACTION_PRIORITY[x.action] ?? 9) - (ACTION_PRIORITY[y.action] ?? 9));
    for (const t of acting) out.push({ group: "act", what: `${t.tradeId}${who}: ${words(t.action)}`, detail: t.reason, to: "/live/positions" });
    const held = acct.trades.filter((t) => QUIET.has(t.action));
    for (const t of held.filter((t) => t.nearestShortDte !== null && t.nearestShortDte <= EXPIRING_DAYS)) {
      out.push({ group: "expiring", what: `${t.tradeId}${who}: short expires in ${String(t.nearestShortDte)}d`, detail: expiryDetail(t), to: "/live/positions" });
    }
    for (const t of held.filter(earningsInLife)) {
      out.push({
        group: "earnings",
        what: `${t.tradeId}${who}: reports ${t.earnings?.date ?? ""}`,
        detail: `before its nearest short expires (${String(t.nearestShortDte ?? "—")}d)`,
        to: "/live/positions",
      });
    }
    const fits = acct.portfolio?.candidates?.fitsTotal ?? 0;
    if (fits > 0) {
      out.push({
        group: "income",
        what: `${String(fits)} income trade${fits === 1 ? "" : "s"} fit the account's room${who}`,
        detail: "sized to the delta and theta room; by symbol, not ranked: no structure has a tested edge",
        to: "/live/portfolio",
      });
    }
  }
  return out;
}

function expiryDetail(t: ManageTrade): string {
  return t.action === "SETTLES" ? "it settles at expiry" : `${t.nearestShortExpiry ?? ""}; the evaluation holds it, exit levels resting`;
}

const HEAD: Record<TodoGroup, string> = {
  person: "a live loop needs a person",
  act: "act on",
  expiring: "expiring this week",
  earnings: "earnings before expiry",
  income: "income",
};

/**
 * `evaluated` is the position evaluation's session, or null when it has not been read: then the list
 * holds only the live loops, and says so, so an empty list never reads as "nothing on the account".
 */
export function WhatToDoList({ items, evaluated }: { items: TodoItem[]; evaluated: string | null }) {
  const groups = (Object.keys(HEAD) as TodoGroup[]).filter((g) => items.some((i) => i.group === g));
  return (
    <section className="card">
      <div className="card-head">
        <h2>what to do today</h2>
        <span className="card-asof">{evaluated !== null ? `positions as evaluated ${evaluated}` : "positions not read"}</span>
      </div>
      {evaluated === null && (
        <p className="muted">The position evaluation has not been read, so only the live loops are listed.</p>
      )}
      {items.length === 0 ? (
        <p className="muted">
          Nothing needs a person on the live loops
          {evaluated !== null ? ", and no position has an action, an expiry this week or a report before it" : ""}.
        </p>
      ) : (
        groups.map((g) => (
          <div key={g} className="todo-group">
            <div className="mf-nav-group">{HEAD[g]}</div>
            <ul style={{ margin: "0 0 0.5rem", paddingLeft: "1.1rem" }}>
              {items
                .filter((i) => i.group === g)
                .map((i, n) => (
                  <li key={`${g}-${String(n)}`}>
                    <Link to={i.to} className={g === "person" ? "pnl-neg" : undefined}>
                      {i.what}
                    </Link>{" "}
                    <span className="muted">— {i.detail}</span>
                  </li>
                ))}
            </ul>
          </div>
        ))
      )}
      <p className="muted" style={{ fontSize: 11, margin: 0 }}>Advice only: nothing on this list places or changes an order.</p>
    </section>
  );
}
