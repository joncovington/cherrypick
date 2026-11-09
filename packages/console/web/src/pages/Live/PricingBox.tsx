import type { ManageAccount, ManagePayload } from "@console/shared";
import { HelpSection, HelpWarn } from "../../components/CardHelp";
import type { Box } from "./PortfolioBox";

/**
 * How the evaluation prices a position (Live → positions, `?box=pricing`), 2026-11-07: what is the
 * broker's and what is modelled, the rate and each name's dividend yield it modelled at, and the legs
 * it could not model. The method is packages/manage `pricing.py` and `_legs`; this box only shows it.
 */

export interface PricingRow {
  name: string;
  /** The yield the name's legs were modelled at; null on an evaluation from before it was carried. */
  divYield: number | null;
  divSource: "history" | "none" | null;
  legs: number;
  broker: number;
  unpriceable: number;
}

/** One row per name the evaluation holds options on, in name order. */
export function pricingByName(accounts: ManageAccount[]): PricingRow[] {
  const rows = new Map<string, PricingRow>();
  for (const a of accounts) {
    for (const t of a.trades) {
      for (const l of t.legs) {
        if (l.expiration === null) continue; // shares: priced at the stock's mark, nothing to model
        const row = rows.get(t.underlying) ?? {
          name: t.underlying,
          divYield: null,
          divSource: null,
          legs: 0,
          broker: 0,
          unpriceable: 0,
        };
        row.legs += 1;
        if (l.ivSource === "broker") row.broker += 1;
        if (l.ivSource === "unpriceable") row.unpriceable += 1;
        if (row.divYield === null && l.divYield !== null) row.divYield = l.divYield;
        if (row.divSource === null && l.divSource !== null) row.divSource = l.divSource;
        rows.set(t.underlying, row);
      }
    }
  }
  return [...rows.values()].sort((x, y) => x.name.localeCompare(y.name));
}

function rateOf(rules: Record<string, unknown> | null | undefined): number | null {
  const pricing = rules?.["pricing"];
  const rate = pricing && typeof pricing === "object" ? (pricing as Record<string, unknown>)["rate"] : null;
  return typeof rate === "number" ? rate : null;
}

const pctText = (v: number) => `${(v * 100).toFixed(2)}%`;

function yieldText(r: PricingRow): string {
  if (r.divYield === null) return "not carried";
  if (r.divSource === "none") return "0% (no dividend record)";
  return pctText(r.divYield);
}

export function PricingMethod({ rate }: { rate: number | null }) {
  return (
    <div className="help-body pricing-help">
      <HelpSection title="What is the broker's">
        <p>
          Every mark is the broker&apos;s: each leg&apos;s mid (halfway between its bid and ask), and the trade&apos;s value and
          P&amp;L from those mids. Nothing on this page reprices what a trade is worth today.
        </p>
      </HelpSection>
      <HelpSection title="What is modelled">
        <p>
          Anything about a price other than today&apos;s: the payoff at the nearest expiry, a calendar&apos;s price stop, what a
          roll would cost, a diagonal&apos;s best case, delta and theta, and the stress figures. For those, each leg&apos;s implied
          volatility is solved from its own mid, and the leg is then priced at that volatility at other prices and dates.
        </p>
        <p>
          The model is Black-Scholes-Merton: an interest rate of {rate === null ? "the ruleset's" : pctText(rate)} (
          <code>pricing.rate</code>), and the name&apos;s dividend yield, which is the dividends gone ex in the last year over
          today&apos;s price. A call is never valued under what exercising it would pay.
        </p>
      </HelpSection>
      <HelpSection title="Why the dividend matters">
        <p>
          A call&apos;s holder does not receive the dividends, so on a high-yield name a call is worth less than a model
          without them says. A long-dated call on a fund paying 4% can trade below the least a no-dividend model allows,
          and then no volatility reproduces its mid at all. Until 2026-11-07 the evaluation left dividends out, and such a
          leg was left unmodelled.
        </p>
      </HelpSection>
      <HelpSection title="When the broker's volatility is used">
        <p>
          Only for a leg whose quote is wide, where a mid is the midpoint of a market nobody quoted seriously, and only when
          the broker&apos;s volatility, priced in this model, lands inside that leg&apos;s bid and ask. The broker figures its
          greeks with no interest rate or dividend, so its volatility cannot simply be dropped into this model: on a
          long-dated call the two can differ by several points.
        </p>
      </HelpSection>
      <HelpWarn title="Unpriceable legs">
        <p>
          A leg no volatility prices is shown as <em>unpriceable</em> and left out of every modelled figure, never counted as
          zero. A trade holding one has no payoff profile, and any rule that needs one falls back or stays silent.
        </p>
      </HelpWarn>
      <HelpSection title="What it is not">
        <p>
          A model of what a trade could do, not a price anyone can trade at. It is European with a smooth yield, so it
          ignores when a dividend actually falls, and it can differ by cents from the payoff chart, which prices American
          options properly.
        </p>
      </HelpSection>
    </div>
  );
}

export function PricingTable({ rows }: { rows: PricingRow[] }) {
  if (rows.length === 0) return <p className="muted">No option legs in this evaluation.</p>;
  return (
    <table className="data-table lv-rules">
      <thead>
        <tr>
          <th>name</th>
          <th>dividend yield</th>
          <th>option legs</th>
          <th>broker&apos;s IV</th>
          <th>unpriceable</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.name}>
            <td>{r.name}</td>
            <td className={r.divSource === "history" ? undefined : "muted"}>{yieldText(r)}</td>
            <td>{r.legs}</td>
            <td>{r.broker || "—"}</td>
            <td className={r.unpriceable ? "pnl-neg" : undefined}>{r.unpriceable || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function pricingBox(data: Pick<ManagePayload, "accounts" | "rules" | "session"> | undefined): Box {
  return {
    id: "pricing",
    kicker: "position evaluation ›",
    title: "How positions are priced",
    tabs: [
      { id: "method", label: "method" },
      { id: "names", label: "by name" },
    ],
    body: (tab) =>
      tab === "names" ? (
        <>
          <p className="muted">
            The yield and IV source each name&apos;s legs were modelled at in the {data?.session ?? "—"} evaluation.
          </p>
          <PricingTable rows={pricingByName(data?.accounts ?? [])} />
        </>
      ) : (
        <PricingMethod rate={rateOf(data?.rules)} />
      ),
  };
}
