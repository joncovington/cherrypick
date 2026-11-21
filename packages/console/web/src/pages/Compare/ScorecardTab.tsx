import { useState } from "react";
import { Link } from "react-router-dom";
import type { Scorecard, ScorecardRow } from "@console/shared";
import { Card, PnlCell, fmtMoney } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { armHref } from "../../components/armpage/ModuleArmPage";
import { useScorecard } from "../../lib/api";

/**
 * Compare's scorecard: one row per arm and automation in the suite, from the orchestrator's `scorecard`
 * (`readers/scorecard.ts`). Each reading and the reason a row needs a look are Python's; the page lays
 * them out, links each row to its page, and filters to the rows with a look.
 */

/** Modules whose arms have a page of their own, and where it is. */
const ARM_PAGES = new Set(["meic", "calendars", "contango", "ladder", "earnings"]);

export function rowHref(r: Pick<ScorecardRow, "module" | "arm">): string | null {
  if (r.module === "automations") return `/automations/automation/${encodeURIComponent(r.arm)}`;
  return ARM_PAGES.has(r.module) ? armHref(r.module, r.arm) : null;
}

const HELP: CardHelpContent = {
  title: "Scorecard",
  body: (
    <>
      <HelpSection title="What each column is">
        <p>
          <b>Research</b> is the register&apos;s verdict for the study the arm or automation carries forward. <b>vs backtest</b> is its forward
          check: collecting until its declared sample, then confirm, drift or kill against the backtest. <b>vs control</b> is the changes
          reading against the arm it is measured beside. <b>Sample</b> is how far it has come: the forward check&apos;s count, else the sessions
          the control reading needs. Net and drawdown are paper&apos;s over the module&apos;s current era.
        </p>
      </HelpSection>
      <HelpSection title="Needs a look">
        <p>
          The first of these that holds, worst first: its forward check reads kill; it reads drift; a decision against its control is due; its
          session net is below zero beyond chance over ten sessions or more; its study failed and it still runs. A forward state set against
          a licensed backtest is hidden while licensed results are, and then calls for no look.
        </p>
      </HelpSection>
    </>
  ),
};

const READING_WORDS: Record<string, string> = {
  ready: "ready",
  leaning: "leaning",
  waiting: "waiting",
  too_early: "too early",
  closed: "closed",
};

function chip(state: string | null): string {
  if (state === "kill" || state === "failed") return "chip chip-bad";
  if (state === "drift" || state === "ready") return "chip chip-warn";
  if (state === "confirm" || state === "passed") return "chip chip-ok";
  return "chip";
}

function VsBacktest({ r }: { r: ScorecardRow }) {
  const vb = r.vs_backtest;
  if (vb === null) return <span className="muted">none declared</span>;
  if (vb.withheld) return <span className="muted">withheld (licensed)</span>;
  return <span className={chip(vb.state)}>{vb.state ?? "—"}</span>;
}

function VsControl({ r }: { r: ScorecardRow }) {
  const vc = r.vs_control;
  if (vc === null) return <span className="muted">{r.kind === "arm" ? "is a control" : "—"}</span>;
  const dir = vc.direction === "better" ? "better" : vc.direction === "worse" ? "worse" : null;
  return (
    <span>
      <span className={chip(vc.reading)}>{READING_WORDS[vc.reading] ?? vc.reading}</span>
      {dir !== null && <span className="muted"> {dir} than {vc.against}</span>}
    </span>
  );
}

function sampleWords(r: ScorecardRow): string {
  const s = r.sample;
  if (s.need !== null) return `${String(s.have)} of ${String(s.need)}`;
  return `${String(s.have)} session${s.have === 1 ? "" : "s"}`;
}

export function ScorecardBody({ doc, onlyLooks }: { doc: Scorecard; onlyLooks: boolean }) {
  if (doc.error !== null) return <p className="error">{doc.error}</p>;
  const rows = onlyLooks ? doc.rows.filter((r) => r.look !== null) : doc.rows;
  const total = doc.rows.filter((r) => r.look !== null).length;
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {doc.rows.length} arms and automations; {total === 0 ? "none needs a look" : `${String(total)} need a look`}
        {doc.looks
          .filter((l) => (doc.counts[l.key] ?? 0) > 0)
          .map((l) => ` · ${String(doc.counts[l.key])} ${l.words}`)
          .join("")}
        .
      </p>
      {rows.length === 0 ? (
        <p className="muted">Nothing needs a look.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table scorecard-table">
            <thead>
              <tr>
                <th>arm</th>
                <th>needs a look</th>
                <th>research</th>
                <th>vs backtest</th>
                <th>vs control</th>
                <th>sample</th>
                <th>net</th>
                <th>drawdown</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const href = rowHref(r);
                return (
                  <tr key={`${r.module}/${r.arm}`} className={r.look !== null ? "sc-look" : undefined}>
                    <td className="sc-arm">
                      {href === null ? r.arm : <Link to={href}>{r.arm}</Link>}
                      <div className="muted sc-module">{r.module}</div>
                    </td>
                    <td className="sc-words">{r.look_words ?? <span className="muted">—</span>}</td>
                    <td>{r.research?.verdict ? <span className={chip(r.research.verdict)}>{r.research.verdict.replace(/_/g, " ")}</span> : <span className="muted">—</span>}</td>
                    <td>
                      <VsBacktest r={r} />
                    </td>
                    <td className="sc-wrap">
                      <VsControl r={r} />
                    </td>
                    <td>{sampleWords(r)}</td>
                    <td>
                      <PnlCell v={r.net} />
                    </td>
                    <td>{r.max_drawdown === null ? "—" : fmtMoney(-r.max_drawdown)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function ScorecardTab() {
  const q = useScorecard();
  const [onlyLooks, setOnlyLooks] = useState(false);
  return (
    <div className="pf-layout view-fade">
      <Section concept="edge" tag="scorecard" question="Which arms and automations need a look?">
        <Card
          title="every arm and automation"
          collapseKey="compare-scorecard"
          className="lv-12"
          help={HELP}
          updatedAt={q.dataUpdatedAt}
          isError={q.isError}
          controls={
            <label className="muted" style={{ fontSize: 12 }}>
              <input type="checkbox" checked={onlyLooks} onChange={(e) => setOnlyLooks(e.target.checked)} /> only those needing a look
            </label>
          }
        >
          {q.data === undefined ? <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read the scorecard." : "Reading every module…"}</p> : <ScorecardBody doc={q.data} onlyLooks={onlyLooks} />}
        </Card>
      </Section>
    </div>
  );
}
