import type { ReactNode } from "react";
import type { ManageWheelBand } from "@console/shared";
import { parseSuiteTs } from "../../lib/etTime";
import { fmtMoney } from "../../lib/format";

/**
 * The small pieces the evaluation's tabs share (positions, wheel and pmcc): the action chip, the
 * detail card, label/value rows, a toned dollar figure, the per-share and fee formats, and the status,
 * tested and check-roll chips. Moved out of ManagePage.tsx so the wheel and PMCC cards can use them
 * without importing each other or the page.
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

/** A per-share price, labelled as one. */
export function share(v: number | null): string {
  return v === null ? "—" : `${v.toFixed(2)} a share`;
}

export function px(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

export function strike(v: number | null): string {
  return v === null ? "—" : String(v);
}

/** Fees as a cost: manage writes them as the broker does (negative); shown positive, subtracted once. */
export function feesPaid(v: number | null): string {
  return v === null ? "—" : fmtMoney(Math.abs(v));
}

export function yesNo(v: boolean | null, yes: string, no: string): string {
  return v === null ? "—" : v ? yes : no;
}

const STATUS_CHIP: Record<string, string> = { open: "chip", complete: "chip chip-hold", unreconciled: "chip chip-warn" };

/** A wheel cycle's or a PMCC's status: open, complete, or unreconciled (amber). */
export function StatusChip({ status }: { status: string }) {
  return <span className={STATUS_CHIP[status] ?? "chip"}>{status}</span>;
}

/** Manage's `tested` flag. Null is "no price for the underlying", never "not tested". */
export function TestedChip({ tested }: { tested: boolean | null }) {
  if (tested === null) return <span className="chip muted">no price</span>;
  return tested ? <span className="chip chip-warn">tested</span> : <span className="chip">not tested</span>;
}

/** A short's skate band and where its strike sits against it, as manage wrote them on a wheel check
 *  row or a PMCC short. */
interface BandedShort {
  band: ManageWheelBand | null;
  bandMissing: string | null;
  beyondBand: { fixed: boolean | null; byExpiry: boolean | null } | null;
}

const NO_BAND = "band not in the snapshot";

/** Manage's reasons for a missing band, in this table's words. "Not in the snapshot" is kept for
 *  the case it means -- no band file at all; a file without this name or expiry says that instead. */
const BAND_MISSING: Record<string, string> = {
  "no band for the name in the snapshot": "no band for this name",
  "no band for this expiry": "no band for this expiry",
};

/** The band's levels, or why there are none: no band file in the snapshot, or a file with no row
 *  for this name or expiry (manage's reason, never folded into "not in the snapshot"). */
export function bandText(r: BandedShort): string {
  if (r.band === null) {
    if (!r.bandMissing || r.bandMissing === NO_BAND) return NO_BAND;
    return BAND_MISSING[r.bandMissing] ?? r.bandMissing;
  }
  const parts = [];
  if (r.band.fixed !== null) parts.push(`fixed ${px(r.band.fixed)}`);
  if (r.band.byExpiry !== null) parts.push(`by expiry ${px(r.band.byExpiry)}`);
  return parts.length ? parts.join(" · ") : NO_BAND;
}

/** Manage's beyond-the-band flags, per band; never compared here. */
export function beyondText(r: BandedShort): string {
  const b = r.beyondBand;
  if (b === null) return "—";
  const out = [];
  if (b.fixed !== null) out.push(`fixed: ${b.fixed ? "beyond" : "inside"}`);
  if (b.byExpiry !== null) out.push(`by expiry: ${b.byExpiry ? "beyond" : "inside"}`);
  return out.length ? out.join(" · ") : "—";
}

/** A suite timestamp on the session's clock, `2026-10-21 16:35 ET`, whatever offset it was written
 *  in (the snapshot carries the host's, e.g. -0600; the income reading UTC). Null in, null out. */
export function etStamp(ts: string | null | undefined): string | null {
  const ms = parseSuiteTs(ts ?? null);
  if (ms === null) return null;
  return `${new Date(ms).toLocaleString("sv-SE", { timeZone: "America/New_York" }).slice(0, 16)} ET`;
}

/** How long ago, in the unit a person reads: `12 min`, `3 h 05 min`, `2 days`. */
export function ageText(ms: number): string {
  const min = Math.max(0, Math.floor(ms / 60_000));
  if (min < 60) return `${min} min`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")} min`;
  return `${Math.floor(min / 1440)} days`;
}

/** check-roll's verdict chip. */
export const VERDICT_CHIP: Record<string, string> = { ok: "chip", risky: "chip chip-warn", reject: "chip chip-bad" };
