import type { PnlTrace, PnlTraceRun, TradingMode } from "@console/shared";
import { usePnlTrace } from "../../lib/api";

/**
 * One position's trace (core.trace), under its excursion in the calendar drill: how it got in, and
 * what was decided while it was held. Read, never derived: which attempt filled, the runs before it
 * and the management runs are core's. A part a module does not record says why.
 */

const ENTRY_BASIS: Record<string, string> = {
  linked: "the module links this attempt to the position",
  matched: "matched by session, arm, symbol and time (it carries the position's strikes)",
  review: "the screening review that selected it",
};

/** `HH:MM`, with `MM-DD` in front when the run spans days. */
function span(r: PnlTraceRun): string {
  const hm = (t: string | null) => (t === null ? "—" : t.slice(11, 16));
  const day = (t: string | null) => (t === null ? "" : t.slice(5, 10));
  if (r.first === r.last) return `${day(r.first)} ${hm(r.first)}`;
  return day(r.first) === day(r.last)
    ? `${day(r.first)} ${hm(r.first)}–${hm(r.last)}`
    : `${day(r.first)} ${hm(r.first)} – ${day(r.last)} ${hm(r.last)}`;
}

function brief(detail: unknown): string {
  if (detail === null || detail === undefined) return "";
  if (typeof detail !== "object") return String(detail);
  return Object.entries(detail as Record<string, unknown>)
    .filter(([, v]) => v !== null && typeof v !== "object")
    .slice(0, 5)
    .map(([k, v]) => `${k} ${typeof v === "number" ? String(Math.round(v * 1000) / 1000) : String(v)}`)
    .join(" · ");
}

function Missing({ what, reason }: { what: string; reason?: string }) {
  return (
    <p className="muted" style={{ fontSize: 11, margin: "0.2rem 0" }}>
      {what}: not recorded — {reason ?? "unknown"}
    </p>
  );
}

function Omitted({ n }: { n: number | undefined }) {
  if (!n) return null;
  return (
    <tr>
      <td colSpan={4} className="muted">
        … {String(n)} more runs in the middle, left out
      </td>
    </tr>
  );
}

export function TraceView({ t }: { t: PnlTrace }) {
  if (!t.ok) return <Missing what="trace" reason={t.error ?? undefined} />;
  if (t.found !== true) return <Missing what="trace" reason={t.reason} />;
  const { entry, orders, management } = t;
  return (
    <div style={{ fontSize: 11 }}>
      {entry?.recorded === true ? (
        <div style={{ margin: "0.3rem 0" }}>
          <div className="muted">
            entry — {ENTRY_BASIS[entry.basis ?? ""] ?? entry.basis}
            {entry.attempts_before !== undefined && ` · ${String(entry.attempts_before)} attempts since the arm's previous fill`}
          </div>
          {entry.review !== undefined ? (
            <div>{brief(entry.review)}</div>
          ) : (
            <table className="data-table">
              <tbody>
                {(entry.runs ?? []).map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{span(r)}</td>
                    <td>{r.outcome}</td>
                    <td>×{String(r.count)}</td>
                    <td className="muted">{r.block_detail ?? brief(r.detail)}</td>
                  </tr>
                ))}
                <Omitted n={entry.runs_omitted} />
                {entry.filled !== undefined && (
                  <tr>
                    <td className="mono">{entry.filled.at === null ? "—" : entry.filled.at.slice(11, 16)}</td>
                    <td className="pnl-pos">filled</td>
                    <td />
                    <td className="muted">{brief(entry.filled.detail)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <Missing what="entry attempts" reason={entry?.reason} />
      )}
      {orders?.recorded === true && (
        <div style={{ margin: "0.3rem 0" }}>
          <div className="muted">orders — the live loop's own record of each leg's order</div>
          <table className="data-table">
            <tbody>
              {(orders.orders ?? []).map((o, i) => (
                <tr key={i}>
                  <td className="mono">{o.at === null ? "—" : o.at.slice(11, 16)}</td>
                  <td>{String(o["leg"] ?? "")}</td>
                  <td>{String(o["outcome"] ?? "")}</td>
                  <td className="muted">{brief({ limit: o["limit_price"], mid: o["mid_at_submit"], fill: o["broker_fill_price"], gap: o["fill_mid_gap"] })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {management?.recorded === true ? (
        <div style={{ margin: "0.3rem 0" }}>
          <div className="muted">
            management — {String(management.events ?? 0)} events, consecutive identical ones folded; an executed one stands alone
          </div>
          {(management.runs ?? []).length > 0 && (
            <table className="data-table">
              <tbody>
                {(management.runs ?? []).map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{span(r)}</td>
                    <td className={r.executed === true ? "pnl-pos" : undefined}>
                      {r.action} {r.executed === true ? "(done)" : "(considered)"}
                    </td>
                    <td>
                      {r.reason ?? ""}
                      {r.count > 1 ? ` ×${String(r.count)}` : ""}
                    </td>
                    <td className="muted">
                      {r.gate !== null && r.gate !== undefined ? `held by ${r.gate}` : ""} {brief(r.detail)}
                    </td>
                  </tr>
                ))}
                <Omitted n={management.runs_omitted} />
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <Missing what="management" reason={management?.reason} />
      )}
    </div>
  );
}

export function TradeTrace({ module, position, mode }: { module: string; position: string; mode: TradingMode }) {
  const { data, isLoading, isError } = usePnlTrace(module, position, mode);
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  if (isError || data === undefined) return <Missing what="trace" reason="could not be read" />;
  return <TraceView t={data} />;
}
