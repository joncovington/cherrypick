import { Fragment, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useOpenParam } from "../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost } from "../pages/Flies/FliesBoxes";
import { useAttempts, attemptsQuery, NO_ATTEMPTS_SCOPE, type AttemptsScope } from "./Attempts";
import { HelpSection, HelpWarn, type CardHelpContent } from "./CardHelp";
import { Card } from "./DataTable";
import { FilterGroup, FilterSortButton, FilterSortPanel, FilterSummary, FilterSwitch, SortSection, sortRows, useSortChoice, type SortOption } from "./FilterSort";

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
 *
 * The card holds one line per arm (2026-11-07); the strike-by-strike map, which runs to dozens of rows
 * on a busy book, opens from it in a lightbox.
 */

interface OccupancyLeg {
  arm: string;
  right: "P" | "C";
  strike: number;
  sign: number;
  count: number;
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
function useOccupancy(
  module: "meic" | "flies",
  mode: TradingMode,
  date: string | null,
  scope: AttemptsScope = NO_ATTEMPTS_SCOPE,
) {
  return useQuery<{ tradeDate: string | null; legs: OccupancyLeg[] }>({
    queryKey: ["occupancy", module, mode, date, scope.arm, scope.symbol, scope.era],
    queryFn: async () => {
      const res = await fetch(`/api/${module}/occupancy?${attemptsQuery(mode, date, scope)}`);
      if (!res.ok) throw new Error(`occupancy: HTTP ${res.status}`);
      return (await res.json()) as { tradeDate: string | null; legs: OccupancyLeg[] };
    },
    refetchInterval: 30_000,
  });
}

const LONG = "var(--pos)";
const SHORT = "var(--neg)";

/** One arm's line on the card: what it holds, and how often its strikes refused an entry. */
export interface OccupancyArmRow {
  arm: string;
  longs: number;
  shorts: number;
  strikes: number;
  /** Strikes of this arm that refused at least one entry. */
  ringed: number;
  /** Entries those strikes refused. */
  refused: number;
}

/** The per-arm lines from the legs and the server's exact refusal counts (keys "arm|strike"). */
export function occupancyRows(legs: OccupancyLeg[], blocked: ReadonlyMap<string, number>): OccupancyArmRow[] {
  return [...new Set(legs.map((l) => l.arm))].sort().map((arm) => {
    const mine = legs.filter((l) => l.arm === arm);
    const strikes = [...new Set(mine.map((l) => l.strike))];
    const counts = strikes.map((k) => blocked.get(`${arm}|${k}`) ?? 0);
    return {
      arm,
      longs: mine.filter((l) => l.sign > 0).reduce((n, l) => n + l.count, 0),
      shorts: mine.filter((l) => l.sign < 0).reduce((n, l) => n + l.count, 0),
      strikes: strikes.length,
      ringed: counts.filter((n) => n > 0).length,
      refused: counts.reduce((a, b) => a + b, 0),
    };
  });
}

/** The card's orderings: name, then size (contracts held) and fit (entries its strikes refused). */
export const OCCUPANCY_SORTS: SortOption<OccupancyArmRow>[] = [
  { id: "name", label: "name", hint: "A to Z", key: (r) => r.arm },
  { id: "held", label: "contracts held", hint: "most first", key: (r) => -(r.longs + r.shorts) },
  { id: "refused", label: "entries refused", hint: "most first", key: (r) => -r.refused },
];

const OCCUPANCY_HELP: CardHelpContent = {
  title: "Reading strike occupancy",
  body: (
    <>
      <HelpWarn title="What a double sign would mean">
        <p>
          A cell showing both signs would mean two legs netting out, which the rule exists to prevent. If you ever see one, the rule is not
          binding.
        </p>
      </HelpWarn>
      <HelpSection title="The rule">
        <p>Longs stack and shorts stack, but a long against a short at the same contract is refused: netted legs hide the risk that is really on.</p>
      </HelpSection>
      <HelpSection title="The map">
        <p>
          The strike map opens from this card: one cell per arm, side and strike, green for long and red for short, with the count when more than
          one is stacked. A ringed strike has refused an entry today. The refusal counts come from the server, over every attempt, not the thinned
          timeline.
        </p>
      </HelpSection>
    </>
  ),
};

function StrikeMap({ all, blocked }: { all: OccupancyLeg[]; blocked: ReadonlyMap<string, number> }) {
  const arms = [...new Set(all.map((l) => l.arm))].sort();
  const strikes = [...new Set(all.map((l) => l.strike))].sort((a, b) => b - a);
  return (
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
                            color: "var(--canvas)",
                            fontWeight: 600,
                            // A strike that has actually refused something is
                            // ringed rather than recoloured: it is still an
                            // ordinary long or short, and the sign is the
                            // thing that must stay readable.
                            outline: refusals > 0 ? "2px solid var(--amber-line)" : undefined,
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
        <span style={{ color: LONG }}>+</span> long · <span style={{ color: SHORT }}>−</span> short · ringed = this strike refused an entry today
      </p>
    </div>
  );
}

export function OccupancyMap({
  module,
  mode,
  date = null,
  scope = NO_ATTEMPTS_SCOPE,
}: {
  module: "meic" | "flies";
  mode: TradingMode;
  date?: string | null;
  scope?: AttemptsScope;
}) {
  const { data: occupancy, isLoading } = useOccupancy(module, mode, date, scope);
  const { data: attempts } = useAttempts(module, mode, date, scope);
  const [sort, setSort] = useSortChoice(`occupancy-${module}`, "name");
  const [refusedOnly, setRefusedOnly] = useState(false);
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const lb = useOpenParam("occ");

  const all = occupancy?.legs ?? [];

  // Strikes that actually refused an entry today, per arm — the map's whole point is to show what
  // was sitting there when they did. Server-computed from the full (untinned) rows, since the
  // timeline itself is thinned for the chart and would silently undercount a raw client-side tally.
  const blocked = new Map<string, number>(Object.entries(attempts?.blockedByStrike ?? {}));
  const rows = occupancyRows(all, blocked);
  const option = OCCUPANCY_SORTS.find((s) => s.id === sort.id) ?? OCCUPANCY_SORTS[0];
  const shown = sortRows(refusedOnly ? rows.filter((r) => r.refused > 0) : rows, option, sort.reversed);
  const changed = (refusedOnly ? 1 : 0) + (sort.id !== "name" || sort.reversed ? 1 : 0);
  const reset = () => {
    setRefusedOnly(false);
    setSort({ id: "name", reversed: false });
  };
  const box = {
    id: "map",
    kicker: "strike occupancy ›",
    title: `Strike map${attempts?.tradeDate != null ? ` (${attempts.tradeDate})` : ""}`,
    tabs: [{ id: "map", label: `${new Set(all.map((l) => l.strike)).size} strikes` }],
    body: () => <StrikeMap all={all} blocked={blocked} />,
  };

  return (
    <Card
      title={`Strike occupancy${attempts?.tradeDate != null ? ` (${attempts.tradeDate})` : ""}`}
      collapseKey={`occupancy-${module}`}
      help={OCCUPANCY_HELP}
      controls={all.length > 0 ? <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} /> : null}
    >
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : all.length === 0 ? (
        <p className="muted">no open legs on this day</p>
      ) : (
        <>
          <FilterSummary
            parts={[refusedOnly ? "arms that refused entries" : "every arm", `by ${option?.label ?? sort.id}${sort.reversed ? ", reversed" : ""}`]}
            note={`${String(shown.length)} of ${String(rows.length)} arms`}
            changed={changed}
            onOpen={() => setPanel(true)}
            onReset={reset}
          />
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>arm</th>
                  <th className="num">long</th>
                  <th className="num">short</th>
                  <th className="num">strikes</th>
                  <th className="num">ringed</th>
                  <th className="num">entries refused</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.arm}>
                    <td>{r.arm}</td>
                    <td className="num">{r.longs}</td>
                    <td className="num">{r.shorts}</td>
                    <td className="num">{r.strikes}</td>
                    <td className="num">{r.ringed}</td>
                    <td className="num">{r.refused}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pf-mores pf-push">
            <BoxOpen lb={lb} id="map" hot>
              the strike map
            </BoxOpen>
          </div>
          <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
            <FilterGroup title="show">
              <FilterSwitch label="refused entries" description="only arms whose strikes refused an entry" on={refusedOnly} onChange={setRefusedOnly} />
            </FilterGroup>
            <SortSection sorts={OCCUPANCY_SORTS} choice={sort} fallback="name" onChange={setSort} />
          </FilterSortPanel>
          <FliesBoxHost lb={lb} boxes={[box]} />
        </>
      )}
    </Card>
  );
}
