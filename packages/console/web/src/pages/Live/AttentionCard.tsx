import type { ReactNode } from "react";
import type { ManageAttention, ManageAttentionItem, ManageAttentionLeg, ManageCostCallRow } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { fmtCash } from "../../lib/format";

/**
 * Live → wheel's "needs a look" (2026-11-04, laid out as two cards 2026-11-06): manage's `attention`
 * list, every short option near its strike, breached (in the money) or likely exercised early, worst
 * first, and every leg expiring within the ruleset's days. The level, the distance and the why are
 * manage's; the meter only draws the distance against the alert threshold (full at the strike or
 * through it). It decides nothing. The chip says "breached" where manage's level is "in the money",
 * and the line beside the leg says ITM.
 */

/** What the chip reads for manage's level; a level it has no word for is shown as manage wrote it. */
export function levelLabel(level: string): string {
  return level === "in the money" ? "breached" : level;
}

const LEVEL_CLASS: Record<string, string> = {
  "early exercise": "att-chip is-bad",
  "in the money": "att-chip is-bad",
  near: "att-chip is-warn",
};

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** The distance in words: "1.2% away", or ITM at or through the strike. */
export function distanceWords(distance: number | null): string {
  if (distance === null) return "no price";
  return distance <= 0 ? "ITM" : `${pct(distance)} away`;
}

/** How full the meter is: 100% at or through the strike, 0% at the alert distance. */
export function meterFill(distance: number | null, nearPct: number | null): number {
  if (distance === null || nearPct === null || nearPct <= 0) return 0;
  if (distance <= 0) return 100;
  return Math.max(0, Math.min(100, (1 - distance / nearPct) * 100));
}

/** A contract symbol with its padding collapsed ("XYZ   261218C00060000" -> "XYZ 261218C00060000"),
 *  as manage's wheel writes it. */
export function contractKey(symbol: string | null | undefined): string {
  return (symbol ?? "").split(/\s+/).filter(Boolean).join(" ");
}

/** Manage's list kept to `scope` (names) and `held` (contract symbols, padding collapsed); null keeps
 *  all. The wheel tab passes `held`: a leg on a wheel name that belongs to another trade (a PMCC's
 *  short call) is not the wheel's. */
export function scoped(all: ManageAttention, scope: string[] | null, held: ReadonlySet<string> | null = null): ManageAttention {
  const keep = (x: { underlying: string; symbol?: string | null }) =>
    (scope === null || scope.includes(x.underlying)) && (held === null || held.has(contractKey(x.symbol)));
  return { ...all, items: all.items.filter(keep), expiring: all.expiring.filter(keep) };
}

/** How the cards name what they cover: everything, a wheel name's, or what the wheel holds. */
function coverage(scope: string[] | null, held: ReadonlySet<string> | null): string {
  return held !== null ? " the wheel holds" : scope === null ? "" : " on a wheel name";
}

/** What a row may carry beside manage's reading (the wheel tab's intent chip); null for nothing. */
export type RowExtra = (leg: ManageAttentionLeg) => ReactNode;

function Row({ i, nearPct, extra }: { i: ManageAttentionItem; nearPct: number | null; extra?: RowExtra }) {
  const fill = meterFill(i.distance, nearPct);
  const early = i.level === "early exercise";
  return (
    <li className="lk-li" title={i.why}>
      <div className="lk-li-top">
        <span className="lk-leg">
          {i.underlying} {i.leg} · {distanceWords(i.distance)}
        </span>
        <span className={LEVEL_CLASS[i.level] ?? "att-chip"}>{levelLabel(i.level)}</span>
      </div>
      <div className="att-meter" role="img" aria-label={`${pct(i.distance)} from the strike`}>
        <span className={i.level === "near" ? "att-fill is-warn" : "att-fill is-bad"} style={{ width: `${fill}%` }} />
      </div>
      <span className="lk-sub">
        spot {i.spot === null ? "—" : i.spot.toFixed(2)} · time value {i.extrinsic === null ? "—" : i.extrinsic.toFixed(2)}
        {early ? ` · ${i.why}` : ""}
      </span>
      {extra?.(i)}
    </li>
  );
}

/** The shorts near their strike, in the money or at risk of early exercise. */
export function NearStrikeCard({
  a: all,
  scope = null,
  held = null,
  hideEmpty = false,
  extra,
}: {
  a: ManageAttention;
  scope?: string[] | null;
  held?: ReadonlySet<string> | null;
  /** Show nothing, not an "all clear" card, when no short qualifies (the wheel tab, 2026-11-12). */
  hideEmpty?: boolean;
  extra?: RowExtra;
}) {
  const a = scoped(all, scope, held);
  const of = coverage(scope, held);
  if (hideEmpty && a.items.length === 0) return null;
  return (
    <Card
      title="shorts near their strike"
      className="wh-half"
      help={{
        title: "Needs a look",
        body: (
          <>
            <HelpSection title="Which shorts">
              <p>
                A short{of} within {pct(a.nearPct, 0)} of its strike, breached (in the money), or likely exercised early. Worst first, as
                manage orders them.
              </p>
            </HelpSection>
            <HelpSection title="The meter">
              <p>Empty at the alert distance, full at the strike. The chip is manage's level: near, breached, or early exercise.</p>
            </HelpSection>
          </>
        ),
      }}
    >
      {a.items.length === 0 ? (
        <p className="muted">
          nothing: every short option{of} is further than {pct(a.nearPct, 0)} from its strike
        </p>
      ) : (
        <ul className="lk-list">
          {a.items.map((i) => (
            <Row key={`${i.symbol ?? i.leg}-${i.underlying}`} i={i} nearPct={a.nearPct} extra={extra} />
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Legs expiring within the ruleset's days, with how far each is from its strike. */
export function ExpiringCard({
  a: all,
  scope = null,
  held = null,
  hideEmpty = false,
  extra,
}: {
  a: ManageAttention;
  scope?: string[] | null;
  held?: ReadonlySet<string> | null;
  hideEmpty?: boolean;
  extra?: RowExtra;
}) {
  const a = scoped(all, scope, held);
  const of = coverage(scope, held);
  if (hideEmpty && a.expiring.length === 0) return null;
  return (
    <Card
      title={`expiring in ${a.expiringDays ?? "?"} days`}
      className="wh-half"
      help={{
        title: "Expiring",
        body: (
          <HelpSection title="Which legs">
            <p>Legs{of} whose expiry falls in the next {a.expiringDays ?? "?"} days, with their distance from the strike.</p>
          </HelpSection>
        ),
      }}
    >
      {a.expiring.length === 0 ? (
        <p className="muted">
          nothing{of} expires in {a.expiringDays ?? "?"} days
        </p>
      ) : (
        <ul className="lk-list">
          {a.expiring.map((e) => {
            const key = `${e.symbol ?? e.leg}-${e.underlying}`;
            const leg = (
              <span className="lk-leg">
                {e.underlying} {e.quantity > 0 ? "+" : "−"}
                {Math.abs(e.quantity)} {e.leg} · {e.dte === 0 ? "today" : `${e.dte}d`}
              </span>
            );
            const away = (
              <span className="muted sm" title="from the strike; negative is in the money">
                {e.distance === null ? "—" : `${e.distance < 0 ? "−" : ""}${pct(Math.abs(e.distance))}`}
              </span>
            );
            const more = extra?.(e);
            // A row with something beside it stacks; the plain row stays the flat one it always was.
            return more ? (
              <li key={key} className="lk-li">
                <div className="lk-li-top">
                  {leg}
                  {away}
                </div>
                {more}
              </li>
            ) : (
              <li key={key} className="lk-li lk-li-flat">
                {leg}
                {away}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** Manage's cost-strike rows kept to `scope` (names); null keeps all; none for an evaluation without them. */
export function costCallRows(a: ManageAttention, scope: string[] | null = null): ManageCostCallRow[] {
  const rows = a.costCall?.enabled ? a.costCall.rows : [];
  return rows.filter((r) => scope === null || scope.includes(r.underlying));
}

const COST_CHIP: Record<ManageCostCallRow["status"], string> = {
  pays: "att-chip is-ok",
  below: "att-chip is-dim",
  "no quote": "att-chip is-dim",
};

/** "2026-12-18 50C · 38d": the expiry and strike the cost-strike call is read at. */
function callWords(r: ManageCostCallRow): string {
  if (r.expiration === null || r.strike === null) return "no call";
  return `${r.expiration} ${r.strike}C${r.dte === null ? "" : ` · ${r.dte}d`}${r.window === "fallback" ? " (nearest monthly)" : ""}`;
}

/**
 * The call at each wheel cycle's cost strike (2026-11-21): a cycle holding assigned shares with no call
 * written, and whether the lowest listed call at or above what the shares cost bids the ruleset's share
 * of the stock's price (a public webinar's "rest a sell order for about 2%"). Manage decides; this draws
 * it and places nothing. A name with no quote says "no quote" and why, never "doesn't pay".
 */
export function CostCallCard({
  a,
  scope = null,
  hideEmpty = false,
}: {
  a: ManageAttention;
  scope?: string[] | null;
  hideEmpty?: boolean;
}) {
  const rows = costCallRows(a, scope);
  if (rows.length === 0 && hideEmpty) return null;
  const bar = a.costCall?.minBidPct ?? null;
  return (
    <Card
      title="cost-strike calls"
      className="wh-half"
      help={{
        title: "Cost-strike call",
        body: (
          <>
            <HelpSection title="What it shows">
              <p>
                For each cycle holding shares an assignment delivered, with no call written: the lowest listed call at or above what the
                shares have cost (the wheel basis), on the standard monthly 30 to 45 days out, else the nearest monthly up to 60. It is
                flagged when that call's bid is at least {pct(bar, 0)} of the stock's price: what a resting sell order there would collect.
              </p>
            </HelpSection>
            <HelpSection title="What it does not do">
              <p>
                Nothing is placed. A bid under the bar is shown quietly; a call with no quote says so and why, which is not the same as not
                paying.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      {rows.length === 0 ? (
        <p className="muted">no wheel cycle holds assigned shares without a call written</p>
      ) : (
        <ul className="lk-list">
          {rows.map((r) => (
            <li key={r.underlying} className="lk-li" title={r.why ?? undefined}>
              <div className="lk-li-top">
                <span className="lk-leg">
                  {r.underlying} · {r.shares} sh · cost {r.effectiveCost.toFixed(2)}
                </span>
                <span className={COST_CHIP[r.status]}>{r.status === "pays" ? `bids ${pct(r.bidPct)}` : r.status}</span>
              </div>
              <span className="lk-sub">
                {callWords(r)}
                {r.bid === null
                  ? ""
                  : ` · bid ${r.bid.toFixed(2)} on ${r.spot === null ? "—" : r.spot.toFixed(2)} = ${pct(r.bidPct)} (bar ${pct(r.minBidPct, 0)})`}
                {r.credit === null ? "" : ` · ${fmtCash(r.credit)} for ${r.contracts} contract${r.contracts === 1 ? "" : "s"}`}
                {r.source === null ? "" : ` · ${r.source}`}
              </span>
              {r.status === "no quote" && r.why ? <span className="lk-sub">{r.why}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
