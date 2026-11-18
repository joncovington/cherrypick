import type { FullSetPart, FullSetPartName, FullSetProgress } from "@console/shared";
import { HelpSection } from "../../components/CardHelp";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";
import { useBacktestResultsOn } from "../../lib/backtestResults";
import { fmtCount } from "../../lib/format";

/** The parts in the order the chain fetches them, with what each holds. */
export const PARTS: Array<[FullSetPartName, string, string]> = [
  ["zero", "Same-day", "same-day expiries, every session at 1 minute"],
  ["eod", "End of day", "each weekly, monthly, quarter-end and LEAPS expiry's daily path"],
  ["oi", "Open interest", "every session"],
  ["near", "Near", "expiries 1-7 days out, at 1 minute"],
  ["mid", "Middle", "monthlies 8-90 days out and weeklies to 45, at 5 minutes"],
];

export function pct(p: FullSetPart): string {
  return p.share === null ? "—" : `${(p.share * 100).toFixed(p.share > 0 && p.share < 0.1 ? 1 : 0)}%`;
}

function Meter({ p, label }: { p: FullSetPart; label?: string }) {
  const width = p.share === null ? 0 : Math.min(100, p.share * 100);
  return (
    <span className="q-meter fullset-meter" title={`${fmtCount(p.done)} of ${fmtCount(p.total)} finished${p.refused ? `, ${fmtCount(p.refused)} refused` : ""}`}>
      {label !== undefined ? <span className="q-meter-name">{label}</span> : null}
      <span className="q-meter-track" aria-hidden="true">
        <span className="q-meter-fill" style={{ width: `${width.toFixed(1)}%` }} />
      </span>
      <span className="q-meter-count">
        {pct(p)}
        {p.refused > 0 ? <span className="d-warn"> · {fmtCount(p.refused)} refused</span> : null}
      </span>
    </span>
  );
}

/** The full set's progress: overall, then a row per underlying with a meter per part. */
export function FullSetBody({ d }: { d: FullSetProgress }) {
  if (!d.planned) {
    return (
      <p className="muted">
        Not planned on this machine. <code>python packages/backtest/scripts/fetch_full_set.py plan</code> writes the request files, then{" "}
        <code>queue-chain --study fullset</code> queues the fetches.
      </p>
    );
  }
  const symbols = Object.keys(d.by_symbol);
  return (
    <>
      <div className="fullset-head">
        <Meter p={d.overall} label="All of it" />
        <span className="study-tile-sub">
          {fmtCount(d.overall.done)} of {fmtCount(d.overall.total)} requests · {d.from ?? "—"} → {d.through ?? "—"}
        </span>
      </div>
      <div className="fullset-wrap">
        <table className="data-table fullset-table">
          <thead>
            <tr>
              <th scope="col">Underlying</th>
              {PARTS.map(([key, name, what]) => (
                <th key={key} scope="col" title={what}>
                  {name}
                </th>
              ))}
              <th scope="col">All</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((sym) => {
              const row = d.by_symbol[sym]!;
              return (
                <tr key={sym}>
                  <th scope="row">{sym}</th>
                  {PARTS.map(([key]) => (
                    <td key={key}>
                      <Meter p={row[key]} />
                    </td>
                  ))}
                  <td>
                    <Meter p={row.all} />
                  </td>
                </tr>
              );
            })}
            <tr className="fullset-total">
              <th scope="row">Total</th>
              {PARTS.map(([key]) => (
                <td key={key}>
                  <Meter p={d.by_part[key]} />
                </td>
              ))}
              <td>
                <Meter p={d.overall} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

export function FullSetCard() {
  const q = useBridged<FullSetProgress>(["backtest", "fullset"], "/api/backtest/fullset", 60_000, useBacktestResultsOn());
  const res = q.data;
  return (
    <Card
      title="Full set fetch"
      collapseKey="backtest-fullset"
      updatedAt={q.dataUpdatedAt}
      help={{
        title: "Reading the full set",
        body: (
          <>
            <HelpSection title="What it is">
              <p>
                The option history for SPX, QQQ, XSP, SPY, IWM, GLD and TLT from 2020, fetched by the research queue in the order the columns
                read, one underlying at a time. Each share is the requests its store records finished (an empty answer counts: the vendor held
                nothing) out of those planned.
              </p>
            </HelpSection>
            <HelpSection title="Refused">
              <p>A refused request is asked again by the chain&apos;s second pass. The queue tab shows the row that is running, per underlying.</p>
            </HelpSection>
          </>
        ),
      }}
    >
      {q.isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : res === undefined || !res.ok || res.data === null ? (
        <p className="muted">{res?.error ?? "the full set's progress could not be read"}</p>
      ) : (
        <FullSetBody d={res.data} />
      )}
    </Card>
  );
}
