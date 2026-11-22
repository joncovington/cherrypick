import { Link } from "react-router-dom";
import type { AutomationReadingRow, AutomationsReadings } from "@console/shared";
import { DataCard } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { useAutomationsReadings } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { automationHref } from "./automationsModels";
import { PLACE_CHIP, STATE_WORDS } from "./AutomationWorking";

/**
 * Automations › vs backtest: every automation's paper record against the Builder run pinned as its
 * backtest reference, one row each, with the reading chip its own page's "Is it working?" shows. Every
 * figure is the backtest package's (`read automation-readings`, one read for every pin); the page lays
 * them out and links each row to its automation's reading. The same chip sits on each automation's row
 * in Groups (`ReadingChip`).
 */

const HELP: CardHelpContent = {
  title: "Against its backtest",
  body: (
    <>
      <HelpSection title="The reading">
        <p>
          Where each automation&apos;s paper running net sits in the band eight in ten of its pinned run&apos;s paths stay inside, at its own trade
          count: on track, below or above. Under five trades it says too few. Open a row for the trades on the run&apos;s usual range and the band.
        </p>
      </HelpSection>
      <HelpSection title="No reference">
        <p>
          An automation with no run pinned has nothing to be set against. Pin one of its explore runs in the Builder (Use as reference); a run
          with fewer than thirty trades is too short to serve.
        </p>
      </HelpSection>
      <HelpSection title="A forward read">
        <p>Paper is read against the run until it has the sample its forward rule declares, or the one the run needs; until then it is not a verdict.</p>
      </HelpSection>
    </>
  ),
};

const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${String(Math.round(v * 100))}%`);

/** The chip a reading earns: its state in words, toned by where the running net sits. */
export function readingChip(r: AutomationReadingRow): { cls: string; words: string } {
  if (r.error !== null) return { cls: "chip chip-warn", words: "reading failed" };
  if (r.pinned === null) return { cls: "chip", words: "no reference" };
  if (r.withheld) return { cls: "chip", words: "licensed, hidden" };
  if (r.state === null) return { cls: "chip", words: "reading…" };
  // Pinned, but under the thirty trades a reference needs: not the same as no pin at all.
  if (r.state === "no_reference") return { cls: "chip", words: "run too short" };
  const cls = r.state === "inside" || r.state === "below" || r.state === "above" ? PLACE_CHIP[r.state] : "chip";
  return { cls, words: STATE_WORDS[r.state] ?? r.state };
}

/** The reading chip, linking to the automation's "Is it working?". */
export function ReadingChip({ row }: { row: AutomationReadingRow | undefined }) {
  if (row === undefined) return null;
  const c = readingChip(row);
  return (
    <Link to={`${automationHref(row.name)}#automation-working`} className={c.cls} title={row.error ?? undefined}>
      {c.words}
    </Link>
  );
}

/** Pinned automations first, each part in the Builder's order. */
export function ordered(rows: AutomationReadingRow[]): AutomationReadingRow[] {
  return [...rows.filter((r) => r.pinned !== null), ...rows.filter((r) => r.pinned === null)];
}

export function VsBacktestTable({ data }: { data: AutomationsReadings | undefined }) {
  const rows = ordered(data?.automations ?? []);
  return (
    <DataCard
      title="every automation against its backtest"
      className="lv-12"
      headers={["automation", "group", "reading", "trades", "inside · below · above", "mean trade", "the run's", "entry rate", "the run's"]}
      rowCount={rows.length}
      loading={data === undefined}
      numFrom={3}
      empty={data?.error ?? "no automation configured"}
      help={HELP}
    >
      {rows.map((r) => {
        const p = r.paper;
        const c = p?.counts ?? null;
        const needed = r.sample?.needed ?? null;
        return (
          <tr key={r.name}>
            <td>
              <Link to={automationHref(r.name)}>{r.name}</Link>
            </td>
            <td>{r.group ?? "—"}</td>
            <td>
              <ReadingChip row={r} />
            </td>
            <td>{p === null ? "—" : needed === null ? p.trades : `${String(p.trades)} of ${String(needed)}`}</td>
            <td>{c === null ? "—" : `${String(c.inside)} · ${String(c.below)} · ${String(c.above)}`}</td>
            <td>{p?.mean === null || p?.mean === undefined ? "—" : fmtMoney(p.mean)}</td>
            <td>{r.reference?.mean === null || r.reference?.mean === undefined ? "—" : fmtMoney(r.reference.mean)}</td>
            <td>{pct(p?.entry_rate)}</td>
            <td>{pct(r.reference?.entry_rate)}</td>
          </tr>
        );
      })}
    </DataCard>
  );
}

export function AutomationsVsBacktestPage() {
  const q = useAutomationsReadings();
  const rows = q.data?.automations ?? [];
  const pinned = rows.filter((r) => r.pinned !== null).length;
  return (
    <div className="pf-layout">
      <Section concept="edge" tag="vs backtest" question="Is each automation trading the way its backtest said it would?">
        {q.data !== undefined && pinned === 0 && rows.length > 0 && (
          <p className="muted lv-12">
            No automation has a run pinned as its backtest reference yet, so none can be read. Pin one of an automation&apos;s explore runs in the{" "}
            <Link to="/automations/builder">Builder</Link> (Use as reference).
          </p>
        )}
        <VsBacktestTable data={q.data} />
      </Section>
    </div>
  );
}
