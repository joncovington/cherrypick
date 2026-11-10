import type { ManageAttention, ManageAttentionItem } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";

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

function Row({ i, nearPct }: { i: ManageAttentionItem; nearPct: number | null }) {
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
    </li>
  );
}

/** The shorts near their strike, in the money or at risk of early exercise. */
export function NearStrikeCard({
  a: all,
  scope = null,
  held = null,
}: {
  a: ManageAttention;
  scope?: string[] | null;
  held?: ReadonlySet<string> | null;
}) {
  const a = scoped(all, scope, held);
  const of = coverage(scope, held);
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
            <Row key={`${i.symbol ?? i.leg}-${i.underlying}`} i={i} nearPct={a.nearPct} />
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
}: {
  a: ManageAttention;
  scope?: string[] | null;
  held?: ReadonlySet<string> | null;
}) {
  const a = scoped(all, scope, held);
  const of = coverage(scope, held);
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
          {a.expiring.map((e) => (
            <li key={`${e.symbol ?? e.leg}-${e.underlying}`} className="lk-li lk-li-flat">
              <span className="lk-leg">
                {e.underlying} {e.quantity > 0 ? "+" : "−"}
                {Math.abs(e.quantity)} {e.leg} · {e.dte === 0 ? "today" : `${e.dte}d`}
              </span>
              <span className="muted sm" title="from the strike; negative is in the money">
                {e.distance === null ? "—" : `${e.distance < 0 ? "−" : ""}${pct(Math.abs(e.distance))}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
