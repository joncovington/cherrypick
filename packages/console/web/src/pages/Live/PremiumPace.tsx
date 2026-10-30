import type { ManageBand, ManagePremium } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Bullet } from "../../components/grid/Bullet";
import { fmtMoney } from "../../lib/format";
import { Money } from "./manageParts";

/**
 * The premium pace (packages/manage `premium.py`): theta carried against its target, and premium
 * collected beside premium kept. Every figure is manage's. The page adds up the months for the
 * totals row, and nothing else.
 *
 * Collected and kept are timed differently: a credit counts in the month it was sold, and what it
 * kept counts in the month the leg went flat. So one month's pair is not a ratio. The six months
 * together are the comparison, and the gap between them is the point: a book that keeps far less
 * than it collects is paying for its premium in losses.
 */

const RIGHT = { textAlign: "right" } as const;

function monthLabel(m: string): string {
  const [y, mo] = m.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${names[Number(mo) - 1] ?? mo} ${y?.slice(2) ?? ""}`;
}

function pct(v: number | null, digits = 2): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function PremiumPaceCard({ premium, thetaBand }: { premium: ManagePremium | null; thetaBand: ManageBand | null }) {
  if (!premium) return null;
  const { target, months, week } = premium;
  const sum = (pick: (m: (typeof months)[number]) => number | null) =>
    months.reduce((s, m) => s + (pick(m) ?? 0), 0);
  const scaleTop = Math.max(thetaBand?.high ?? 0, thetaBand?.value ?? 0, target.pctNlv ?? 0) * 1.25 || 0.004;
  return (
    <Card title="premium pace">
      <div className="pp-target">
        <Bullet
          min={0}
          max={scaleTop}
          value={thetaBand?.value ?? null}
          band={thetaBand && thetaBand.low !== null && thetaBand.high !== null ? [thetaBand.low, thetaBand.high] : null}
          marker={target.pctNlv}
          label={`theta ${pct(thetaBand?.value ?? null)} of net liq a day against a target of ${pct(target.pctNlv)}`}
        />
        <p className="pp-line">
          theta {fmtMoney(target.theta)} a day against a target of {fmtMoney(target.dollars)} ({pct(target.pctNlv)} of net
          liq)
          {target.ratio !== null && <span className="muted"> · {(target.ratio * 100).toFixed(0)}% of target</span>}
        </p>
      </div>
      {premium.error ? (
        <p className="muted">collected and kept: {premium.error}</p>
      ) : (
        <>
          {week && (
            <p className="pp-line">
              this week: collected {fmtMoney(week.collected)} on {week.orders} order{week.orders === 1 ? "" : "s"} · kept{" "}
              <Money v={week.kept} /> on {week.closed} leg{week.closed === 1 ? "" : "s"} closed
            </p>
          )}
          <table className="pp-months">
            <thead>
              <tr>
                <th>month</th>
                <th style={RIGHT}>collected</th>
                <th style={RIGHT}>kept</th>
                <th style={RIGHT}>legs closed</th>
              </tr>
            </thead>
            <tbody>
              {months.map((m) => (
                <tr key={m.month} data-month={m.month}>
                  <td>{monthLabel(m.month)}</td>
                  <td style={RIGHT}>{fmtMoney(m.collected)}</td>
                  <td style={RIGHT}>
                    <Money v={m.kept} />
                  </td>
                  <td style={RIGHT}>{m.closed}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>{months.length} months</td>
                <td style={RIGHT}>{fmtMoney(sum((m) => m.collected))}</td>
                <td style={RIGHT}>
                  <Money v={Math.round(sum((m) => m.kept) * 100) / 100} />
                </td>
                <td style={RIGHT}>{sum((m) => m.closed)}</td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
      <p className="muted pf-caption">
        collected is an opening order's net credit before fees (a roll at its net), in the month it filled; kept is an
        option leg's net cash after fees, in the month it went flat, so compare them over the months, not within one
        {premium.zeroDte && premium.zeroDte.legs > 0 && (
          <>
            {" "}
            · left out: {premium.zeroDte.legs} 0DTE leg{premium.zeroDte.legs === 1 ? "" : "s"} on{" "}
            {premium.zeroDte.underlyings.join(", ")} (the live loops' own, {fmtMoney(premium.zeroDte.net)})
          </>
        )}
        {premium.unmatched && premium.unmatched.legs > 0 && (
          <>
            {" "}
            · {premium.unmatched.legs} leg{premium.unmatched.legs === 1 ? "" : "s"} opened before the history (
            {fmtMoney(premium.unmatched.net)}, incomplete)
          </>
        )}{" "}
        · theta is paid for by gamma, so a month far above the pace most likely came from the market
      </p>
    </Card>
  );
}
