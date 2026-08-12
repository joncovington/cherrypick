import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";

/**
 * The strike-occupancy map: which contracts each arm holds, and on which side.
 *
 * This is the sign rule made legible. The rule refuses a new leg that would sit
 * OPPOSITE an open one at the same (expiry, right, strike) — because two legs
 * netting to zero mean the ledger's recorded risk is not the risk on — and a
 * refusal is otherwise just a reason string. Here you can see the strike that
 * caused it and what was already sitting there.
 *
 * Longs and shorts get their own colour, not their own column: same-sign
 * stacking is legal and common (two flies sharing a wing, a condor nested
 * inside another), so the useful reading is "which sign is at this strike",
 * and the thing that must never appear is both signs at one contract.
 */

interface OccupancyLeg {
  arm: string;
  right: "P" | "C";
  strike: number;
  sign: number;
  count: number;
}

interface AttemptRow {
  arm: string;
  outcome: string;
  blockingStrike: number | null;
  ts: string | null;
}

interface AttemptsPayload {
  tradeDate: string | null;
  timeline: AttemptRow[];
}

/**
 * The legs come from the server, already derived.
 *
 * The arithmetic that turns a stored row into contracts — an IC's wings sit
 * `wing_width` outside its shorts, a fly's centre is doubled — is written once
 * per module in Python and once more in `readers/occupancy.ts`. Deriving it a
 * third time here would be a third place to disagree about what the book holds,
 * and a page that renders occupancy differently from the gate enforcing it is
 * worse than no page.
 */
function useOccupancy(module: "meic" | "flies", mode: TradingMode, date: string | null) {
  return useQuery<{ tradeDate: string | null; legs: OccupancyLeg[] }>({
    queryKey: ["occupancy", module, mode, date],
    queryFn: async () => {
      const qs = new URLSearchParams({ mode });
      if (date !== null) qs.set("date", date);
      const res = await fetch(`/api/${module}/occupancy?${qs.toString()}`);
      if (!res.ok) throw new Error(`occupancy: HTTP ${res.status}`);
      return (await res.json()) as { tradeDate: string | null; legs: OccupancyLeg[] };
    },
    refetchInterval: 30_000,
  });
}

function useBlockingStrikes(module: "meic" | "flies", mode: TradingMode, date: string | null) {
  return useQuery<AttemptsPayload>({
    queryKey: ["attempts", module, mode, date],
    queryFn: async () => {
      const qs = new URLSearchParams({ mode });
      if (date !== null) qs.set("date", date);
      const res = await fetch(`/api/${module}/attempts?${qs.toString()}`);
      if (!res.ok) throw new Error(`attempts: HTTP ${res.status}`);
      return (await res.json()) as AttemptsPayload;
    },
    refetchInterval: 30_000,
  });
}

const LONG = "#43b57a";
const SHORT = "#d23f57";

export function OccupancyMap({
  module,
  mode,
  date = null,
}: {
  module: "meic" | "flies";
  mode: TradingMode;
  date?: string | null;
}) {
  const { data: occupancy, isLoading } = useOccupancy(module, mode, date);
  const { data: attempts } = useBlockingStrikes(module, mode, date);

  const all = occupancy?.legs ?? [];
  const arms = [...new Set(all.map((l) => l.arm))].sort();
  const strikes = [...new Set(all.map((l) => l.strike))].sort((a, b) => b - a);

  // Strikes that actually refused an entry today, per arm — the map's whole
  // point is to show what was sitting there when they did.
  const blocked = new Map<string, number>();
  for (const row of attempts?.timeline ?? []) {
    if (row.outcome !== "sign_rule_blocked" || row.blockingStrike === null) continue;
    const key = `${row.arm}|${row.blockingStrike}`;
    blocked.set(key, (blocked.get(key) ?? 0) + 1);
  }

  return (
    <section className="card">
      <div className="panel-head-row">
        <h2>Strike occupancy{attempts?.tradeDate != null ? ` (${attempts.tradeDate})` : ""}</h2>
        <span className="muted lbl">longs stack, shorts stack — a long against a short is refused</span>
      </div>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : all.length === 0 ? (
        <p className="muted">no open legs on this day</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ textAlign: "right" }}>strike</th>
                {arms.map((arm) => (
                  <th key={arm} colSpan={2} style={{ textAlign: "center" }}>
                    {arm}
                  </th>
                ))}
              </tr>
              <tr>
                <th />
                {arms.map((arm) => (
                  <Fragment key={arm}>
                    <th className="muted" style={{ textAlign: "center", fontWeight: 400 }}>
                      P
                    </th>
                    <th className="muted" style={{ textAlign: "center", fontWeight: 400 }}>
                      C
                    </th>
                  </Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {strikes.map((strike) => (
                <tr key={strike}>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{strike.toFixed(0)}</td>
                  {arms.flatMap((arm) =>
                    (["P", "C"] as const).map((right) => {
                      const cell = all.find((l) => l.arm === arm && l.right === right && l.strike === strike);
                      const refusals = blocked.get(`${arm}|${strike}`) ?? 0;
                      return (
                        <td
                          key={`${arm}-${right}-${strike}`}
                          style={{ textAlign: "center", padding: "1px 6px" }}
                          title={
                            cell === undefined
                              ? undefined
                              : `${arm} ${right}${strike}: ${cell.sign > 0 ? "long" : "short"} ×${cell.count}` +
                                (refusals > 0 ? ` — refused ${refusals} ${refusals === 1 ? "entry" : "entries"} today` : "")
                          }
                        >
                          {cell !== undefined && (
                            <span
                              style={{
                                display: "inline-block",
                                minWidth: 16,
                                borderRadius: 3,
                                background: cell.sign > 0 ? LONG : SHORT,
                                color: "#0d1014",
                                fontWeight: 600,
                                // A strike that has actually refused something is
                                // ringed rather than recoloured: it is still an
                                // ordinary long or short, and the sign is the
                                // thing that must stay readable.
                                outline: refusals > 0 ? "2px solid #d9a13b" : undefined,
                              }}
                            >
                              {cell.sign > 0 ? "+" : "−"}
                              {cell.count > 1 ? cell.count : ""}
                            </span>
                          )}
                        </td>
                      );
                    }),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 11, marginTop: "0.4rem" }}>
            <span style={{ color: LONG }}>+</span> long · <span style={{ color: SHORT }}>−</span> short · ringed = this
            strike refused an entry today. A cell showing both signs would mean two legs netting out, which the rule
            exists to prevent — if you ever see one, the rule is not binding.
          </p>
        </div>
      )}
    </section>
  );
}
