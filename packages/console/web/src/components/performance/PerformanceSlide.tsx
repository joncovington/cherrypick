import { Card, SortTh, sortRows, useSort } from "../DataTable";
import { fmtMoney, fmtPct } from "../../lib/format";
import type { ExitReasonRow, TradingMode } from "@console/shared";
import { useModulePerformance, type PerformanceModuleId } from "../../lib/api";
import { matchesArm } from "../../lib/armTag";
import { HelpSection, HelpWarn, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { useOpenParam } from "../../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../../pages/Flies/FliesBoxes";
import { MetricTiles } from "./MetricTiles";
import { ExcursionsCard } from "./ExcursionsCard";
import { CumulativeCard } from "./CumulativeCard";
import { PairedABCard } from "./PairedABCard";
import { TradeNetHistogram } from "./TradeNetHistogram";

/** A row's win rate as a fraction, or null for an empty row. */
export function exitWinRate(r: ExitReasonRow): number | null {
  return r.n > 0 ? r.wins / r.n : null;
}

/**
 * The exit-reason tally, sortable once the reader's rows are known to be rows (not `unavailable`).
 * Win rate and average net per reason say where an arm's money goes: a reason that wins most of
 * its trades for little, beside one that loses rarely but large, is the shape the tiles' break-even
 * win rate warns about, broken down by how each trade ended.
 */
export function ExitReasonsBody({ rows }: { rows: ExitReasonRow[] }) {
  const sort = useSort();
  const sorted = sortRows(rows, sort, {
    tag: (r) => r.tag,
    reason: (r) => r.reason,
    n: (r) => r.n,
    win: exitWinRate,
    avg: (r) => r.avgNet,
    net: (r) => r.net,
  });
  const toned = (v: number | null) => (v === null ? "muted" : v >= 0 ? "pnl-pos" : "pnl-neg");
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="tag" k="tag" sort={sort} />
            <SortTh label="reason" k="reason" sort={sort} />
            <SortTh label="n" k="n" sort={sort} />
            <SortTh label="win rate" k="win" sort={sort} />
            <SortTh label="avg net" k="avg" sort={sort} />
            <SortTh label="net" k="net" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const win = exitWinRate(r);
            return (
              <tr key={`${r.tag}-${r.reason}`}>
                <td>{r.tag}</td>
                <td>{r.reason}</td>
                <td>{r.n}</td>
                <td>{fmtPct(win === null ? null : win * 100, 0)}</td>
                <td className={toned(r.avgNet)}>{fmtMoney(r.avgNet)}</td>
                <td className={toned(r.net)}>{fmtMoney(r.net)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** An arm card's ?: the underpowered warning leads when every experiment paired against it is below the gate. */
export function armHelp(underpowered: boolean): CardHelpContent {
  return {
    title: "An arm's reading",
    body: (
      <>
        {underpowered && (
          <HelpWarn title="Underpowered">
            <p>Every experiment paired against this book is still below the promotion gate&apos;s sample and day thresholds.</p>
          </HelpWarn>
        )}
        <HelpSection title="The tiles">
          <p>
            One calibration reading per arm, from the suite&apos;s one metric vocabulary. Net figures are after fees; a tile&apos;s footer is
            the sample it was computed over. The histogram is each trade&apos;s net.
          </p>
        </HelpSection>
      </>
    ),
  };
}

interface EraStamp {
  from: string | null;
  note: string | null;
  moduleEra: string | null;
}

/** The cumulative card's ?: the live ledger and the evidence window lead as warnings when they apply. */
export function cumulativeHelp(live: boolean, era: EraStamp, arm: string | null, controlDrawn: boolean): CardHelpContent {
  return {
    title: "Net P&L over time",
    body: (
      <>
        {live && (
          <HelpWarn title="The live ledger">
            <p>A measurement of the live book, not a promotion reading. Those are paper only, and so are advised A/B pairs.</p>
          </HelpWarn>
        )}
        {era.note !== null && (
          <HelpWarn title="Evidence window">
            <p>
              Scoped to the suite&apos;s current evidence window ({era.from ?? "all time"}): {era.note}
              {era.moduleEra !== null && (
                <> This module&apos;s own {era.moduleEra} era is read by the era stamped on each position; earlier eras are in the history tab.</>
              )}
            </p>
          </HelpWarn>
        )}
        <HelpSection title="The lines">
          <p>
            Cumulative net per arm, one line each. A circle marks a measurement break, on the first arm&apos;s line only.
            {arm !== null && (
              <>
                {" "}
                Showing {arm}, picked in the header{controlDrawn && "; control is drawn as the reference"}.
              </>
            )}
          </p>
        </HelpSection>
      </>
    ),
  };
}

const HERE = { exits: "exit reasons", breaks: "measurement breaks" } as const;

/**
 * The suite-wide performance slide: one calibration reading per arm, via
 * `GET /api/performance/:module` (`server/src/readers/performance.ts`). Generic over every module
 * this reads (curve/pmcc/calendars/bwb/meic/flies/earnings all share ONE schema registry and ONE
 * metric vocabulary -- `core.metrics.calibration_reading`), so this component is not written per
 * module the way each module's OWN richer tab is.
 *
 * A module's `underpowered` verdict rides on its `pairs` entry (the experiment that produced the
 * `advised:` book), not on the base group's own tile row -- a base was never an experiment and
 * has no verdict to carry. Several pairs can share one base (2026-09-17: each experiment has its
 * own book), so the base's flag says "every paired experiment" only when all of them are below
 * the gate.
 *
 * Laid out under the questions it answers (2026-11-07): how each arm did, how it got there, whether
 * an advised arm is ahead of its base, how far positions swung. The exit-reason tally and the
 * measurement breaks are lightboxes from the cumulative card (`?perf=`).
 */
export function PerformanceSlide({
  module,
  mode = "paper",
  arm = null,
}: {
  module: PerformanceModuleId;
  mode?: TradingMode;
  /** The page's arm picker (flies, MEIC): narrows the arm cards, pairs and exit reasons to that arm.
   *  The cumulative chart keeps `control` beside it as the reference. null is every arm. */
  arm?: string | null;
}) {
  const { data, isLoading, dataUpdatedAt } = useModulePerformance(module, "current", mode);
  const lb = useOpenParam("perf");

  if (isLoading || data === undefined) {
    return (
      <div className="pf-layout">
        <Section concept="stand" tag="the record" question="How has each arm done?">
          <Card title="performance">
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          </Card>
        </Section>
      </div>
    );
  }

  if (!data.ok) {
    return (
      <div className="pf-layout">
        <Section concept="stand" tag="the record" question="How has each arm done?">
          <Card title="performance" isError>
            <p className="muted">{data.error ?? "calibration reading unavailable"}</p>
          </Card>
        </Section>
      </div>
    );
  }

  // Per base: true only when every experiment paired against it with a verdict is underpowered.
  const underpoweredByBase = new Map<string, boolean>();
  for (const p of data.pairs) {
    if (p.underpowered === null) continue;
    underpoweredByBase.set(p.base, (underpoweredByBase.get(p.base) ?? true) && p.underpowered);
  }
  const groupByTag = new Map(data.groups.map((g) => [g.tag, g]));
  // The arm picker, applied to what was read. Control stays on the cumulative chart as the line the
  // picked arm is read against; it is drawn there only, not given a card of its own.
  const shown = data.groups.filter((g) => matchesArm(g.tag, arm));
  const control = arm !== null && arm !== "control" ? groupByTag.get("control") : undefined;
  const charted = control !== undefined ? [...shown, control] : shown;

  const exitRows = Array.isArray(data.exitReasons) ? data.exitReasons.filter((r) => matchesArm(r.tag, arm)) : [];
  const pairs = data.pairs
    .filter((p) => matchesArm(p.base, arm) || matchesArm(p.advised, arm))
    // Both sides must actually be present as performance groups in THIS window -- a pair the
    // reader found (advisor.db still remembers the experiment) but whose books have no data
    // in the current era is not something a cumulative chart can show.
    .flatMap((p) => {
      const advisedGroup = groupByTag.get(p.advised);
      const baseGroup = groupByTag.get(p.base);
      return advisedGroup === undefined || baseGroup === undefined ? [] : [{ p, advisedGroup, baseGroup }];
    });

  const tabs: Array<{ id: keyof typeof HERE; label: string }> = [];
  if (exitRows.length > 0) tabs.push({ id: "exits", label: HERE.exits });
  if (data.breaks.length > 0) tabs.push({ id: "breaks", label: HERE.breaks });
  const box: FliesBox = {
    id: "detail",
    kicker: "performance ›",
    title: "Exits and breaks",
    tabs,
    body: (tab) =>
      tab === "breaks" ? (
        <>
          {data.breaks.map((b, i) => (
            <p key={i} className="muted" style={{ fontSize: 12, margin: "0.3rem 0" }}>
              <strong>{b.date}</strong> · {b.key}
              {b.scope !== null && ` (${b.scope})`}
              {b.note !== null && ` — ${b.note}`}
            </p>
          ))}
        </>
      ) : (
        <ExitReasonsBody rows={exitRows} />
      ),
  };

  return (
    <div className="pf-layout">
      {shown.length === 0 ? (
        <Section concept="stand" tag="the record" question="How has each arm done?">
          <Card title="performance" updatedAt={dataUpdatedAt}>
            <p className="muted">
              {arm !== null && data.groups.length > 0 ? `no closed trades for ${arm} in this window` : "no closed trades in this window yet"}
              {data.era.moduleEra !== null && <> (the {data.era.moduleEra} era)</>}
            </p>
          </Card>
        </Section>
      ) : (
        <>
          <Section concept="stand" tag="the record" question="How has each arm done?">
            {shown.map((g) => (
              <Card
                key={g.tag}
                title={g.tag}
                collapseKey={`performance-${module}-${g.tag}`}
                updatedAt={dataUpdatedAt}
                help={armHelp(underpoweredByBase.get(g.tag) === true)}
                flag={underpoweredByBase.get(g.tag) === true ? "underpowered" : undefined}
              >
                <MetricTiles reading={g.reading} peakRisk={g.peakRisk} />
                <TradeNetHistogram nets={g.tradeNets} />
              </Card>
            ))}
          </Section>

          <Section concept="hist" tag="over time" question="How did it get here?">
            <Card
              title="cumulative net P&L"
              className="lv-12"
              collapseKey={`performance-${module}-cumulative`}
              updatedAt={dataUpdatedAt}
              help={cumulativeHelp(data.mode === "live", data.era, arm, control !== undefined)}
              flag={data.mode === "live" ? "live ledger" : data.era.note !== null ? "evidence window" : undefined}
            >
              <CumulativeCard groups={charted} breaks={data.breaks} />
              {tabs.length > 0 && (
                <div className="pf-mores">
                  {tabs.map((x) => (
                    <BoxOpen key={x.id} lb={lb} id="detail" tab={x.id}>
                      {x.label}
                    </BoxOpen>
                  ))}
                </div>
              )}
            </Card>
          </Section>
        </>
      )}

      {pairs.length > 0 && (
        <Section concept="edge" tag="advised arms" question="Is the advised arm ahead of its base?">
          {pairs.map(({ p, advisedGroup, baseGroup }) => (
            <PairedABCard key={p.advised} pair={p} advisedGroup={advisedGroup} baseGroup={baseGroup} />
          ))}
        </Section>
      )}

      {data.excursions.ok && (
        <Section concept="risk" question="How far did positions swing?">
          <ExcursionsCard excursions={data.excursions} />
        </Section>
      )}
      {tabs.length > 0 && <FliesBoxHost lb={lb} boxes={[box]} />}
    </div>
  );
}
