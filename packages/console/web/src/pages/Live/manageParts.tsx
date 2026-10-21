import type { ReactNode } from "react";
import { fmtMoney } from "../../lib/format";

/**
 * The small pieces the evaluation's tabs share (positions and wheel): the action chip, the detail
 * card, label/value rows and a toned dollar figure. Moved out of ManagePage.tsx so the wheel cards can
 * use them without the two files importing each other.
 */

export const ACTION_LABEL: Record<string, string> = {
  CLOSE: "close",
  TAKE_PROFIT: "take profit",
  ROLL_OR_CLOSE: "roll or close",
  ROLL_FOR_CREDIT: "roll for credit",
  CLOSE_LEGS: "close legs",
  REVIEW: "review",
  SETTLES: "settles",
  HOLD: "hold",
};

// An action's chip colour: hold is blue, review amber, anything that exits or changes the trade red.
// SETTLES stays plain: nothing to do but let it expire.
const ACTION_CHIP: Record<string, string> = {
  HOLD: "chip-hold",
  REVIEW: "chip-warn",
  CLOSE: "chip-bad",
  TAKE_PROFIT: "chip-bad",
  ROLL_OR_CLOSE: "chip-bad",
  ROLL_FOR_CREDIT: "chip-bad",
  CLOSE_LEGS: "chip-bad",
};

export function ActionChip({ action }: { action: string }) {
  return <span className={`chip ${ACTION_CHIP[action] ?? ""}`}>{ACTION_LABEL[action] ?? action}</span>;
}

export function tone(v: number | null | undefined): string {
  if (v === null || v === undefined) return "muted";
  return v >= 0 ? "pnl-pos" : "pnl-neg";
}

/** One card in an expanded row: a small caps title, an optional chip, and its body. */
export function DetailCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="md-card">
      <header>
        <h4>{title}</h4>
        {aside}
      </header>
      {children}
    </section>
  );
}

/** Label / value rows. A value is a node so a figure can carry its tone. */
export function Kv({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="md-kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Money({ v }: { v: number | null }) {
  return <span className={tone(v)}>{fmtMoney(v)}</span>;
}
