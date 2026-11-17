import type { BuilderToday } from "@console/shared";

/**
 * "Today, live": the engine's own decision of this automation now, as paper would make it, on a
 * read-only snapshot of the stream cache. Every check it read, and the legs it would take or the check
 * that would stop it. Nothing is placed from this page.
 */

function value(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return typeof v === "string" ? v : JSON.stringify(v);
}

const WHY: Record<string, string> = {
  no_snapshot: "No snapshot to decide on",
  no_symbol: "No stream settings for its symbol",
  no_ledger: "No paper ledger on this machine yet",
  invalid: "The automation as edited would not load",
  not_decided: "The engine took no decision",
};

/** One leg in the suite's order chain: a fixed-width STO/BTO tag, then "1 2026-12-18 585P". */
export function legText(l: { side: number; strike: number; right: string }, quantity: number, expiry: string): string {
  return `${l.side < 0 ? "STO" : "BTO"} ${quantity} ${expiry} ${l.strike}${l.right}`;
}

export function TodayView({ t }: { t: BuilderToday }) {
  const decided = t.state === "would_enter" || t.state === "would_skip";
  return (
    <div className="builder-today" data-state={t.state}>
      {t.window && (
        <p className="muted">
          Entry window {t.window[0]}–{t.window[1]} ET{t.in_window ? ", open now" : `; read at ${t.at?.slice(11, 16) ?? "now"}, outside it`}.
        </p>
      )}
      {!decided && (
        <p>
          {WHY[t.state] ?? t.state}
          {t.reason ? `: ${t.reason}` : ""}
          {t.detail ? ` (${value(t.detail)})` : ""}.
        </p>
      )}
      {t.problems && t.problems.length > 0 && (
        <ul className="builder-problems">
          {t.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {decided && (
        <>
          <div className="table-scroll">
            <table className="builder-run-table">
              <thead>
                <tr>
                  <th scope="col">Check</th>
                  <th scope="col">Value</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {(t.checks ?? []).map((c, i) => (
                  <tr key={`${c.check}-${i}`}>
                    <td>{c.check.replaceAll("_", " ")}</td>
                    <td>{value(c.value)}</td>
                    <td className={c.passed ? "pos" : "neg"}>{c.passed ? "pass" : "fail"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.state === "would_skip" ? (
            <p>
              <strong>Would skip now:</strong> {t.reason?.replaceAll("_", " ")}.
            </p>
          ) : (
            t.entry && (
              <>
                <p>
                  <strong>Would enter now</strong> at a mid of {value(t.entry.prices["mid"])}, max loss {value(t.entry.max_loss)} a share.
                </p>
                <pre className="builder-chain">{t.entry.legs.map((l) => legText(l, t.entry!.quantity, t.entry!.expiry)).join("\n")}</pre>
              </>
            )
          )}
        </>
      )}
      <p className="muted">The paper loop's own checks on today's stream cache. Nothing is placed from this page.</p>
    </div>
  );
}
