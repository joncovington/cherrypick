import { Link, useParams } from "react-router-dom";
import type { ExternalCell, ExternalReport, ExternalReportInvalid, ExternalSection } from "@console/shared";
import { GridCard } from "../../components/grid/GridCard";
import { useExternalReports } from "../../lib/api";

/**
 * External reports: whatever a person's own scripts wrote to `data/external/<id>.json`
 * (`docs/guide/external-reports.md`), drawn the same way whoever or whatever wrote it. No source is
 * named here: the report says its own title and source, and the page shows them as written.
 *
 * One rail entry (`Reports / external`), and a tab strip inside it built from the files in the folder:
 * an overview of every report, then one tab per file at `/reports/external/<id>`. A new file adds a
 * tab and a removed one takes it away, with no config. The strip is the page's own rather than the
 * rail's because the rail is static data (`navGroups.ts`) that the frame checks against each
 * manifest's slide list; a tab list that depends on the server's files cannot live there without
 * breaking that check.
 *
 * Every section is a card titled by its heading, so a script that posts a picture of its own report
 * can capture it by title (`tools/ui-check.mjs --route /reports/external/<id> --card "<heading>"`).
 * A file that fails the contract is a tab saying why and nothing of its content, never half drawn.
 */

const BASE = "/reports/external";

function cell(v: ExternalCell): string {
  if (v === null) return "—";
  return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : v;
}

/** A small table glyph: the card's mark, and what a capture waits for before it takes the card. */
function Glyph() {
  return (
    <svg className="external-glyph" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M1 2h10v8H1zM1 5h10M5 2v8" fill="none" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

/** One barred column's scale: the largest magnitude in the column, and whether any value is negative
 *  (then the track has a zero line in the middle and negatives grow to its left). */
export interface BarScale {
  max: number;
  signed: boolean;
}

/** The scale of each column the table marks with `bars`, within that column only. A null or text cell
 *  has no bar; a column with nothing to scale (all null, or all zero) gets none. */
export function barScales(table: { rows: ExternalCell[][]; bars?: number[] | undefined }): Map<number, BarScale> {
  const out = new Map<number, BarScale>();
  for (const col of table.bars ?? []) {
    const values = table.rows.map((r) => r[col]).filter((v): v is number => typeof v === "number");
    const max = Math.max(0, ...values.map((v) => Math.abs(v)));
    if (max > 0) out.set(col, { max, signed: values.some((v) => v < 0) });
  }
  return out;
}

/** Share of its half of the track a value fills, 0 to 100. */
export function barPercent(value: number, scale: BarScale): number {
  return Math.min(100, (Math.abs(value) / scale.max) * 100);
}

/** An inline bar beside the number, which stays in the cell as text: the bar is a picture of it. */
function Bar({ value, scale }: { value: number; scale: BarScale }) {
  const pct = barPercent(value, scale);
  const negative = value < 0;
  return (
    <span className={`external-bar${scale.signed ? " external-bar-signed" : ""}`} aria-hidden="true">
      {scale.signed && (
        <span className="external-bar-half external-bar-left">
          {negative && <span className="external-bar-fill external-bar-neg" style={{ width: `${String(pct)}%` }} />}
        </span>
      )}
      <span className="external-bar-half external-bar-right">
        {!negative && <span className="external-bar-fill external-bar-pos" style={{ width: `${String(pct)}%` }} />}
      </span>
    </span>
  );
}

function Freshness({ report }: { report: ExternalReport }) {
  return report.stale ? (
    <span className="chip chip-warn" title={`Older than ${String(report.staleAfterHours)} hours`}>
      stale
    </span>
  ) : (
    <span className="chip chip-ok">fresh</span>
  );
}

function Section({ report, section }: { report: ExternalReport; section: ExternalSection }) {
  const foot = (
    <>
      <Glyph /> {report.source}, as of {report.asOf}
    </>
  );
  if ("text" in section) {
    return (
      <GridCard label={section.heading} span={12} h={96} className="external-card" foot={foot}>
        <p className="external-text">{section.text}</p>
      </GridCard>
    );
  }
  const { columns, rows } = section.table;
  const scales = barScales(section.table);
  return (
    <GridCard label={section.heading} span={12} h={96} className="external-card" foot={foot}>
      {rows.length === 0 ? (
        <p className="muted">No rows.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r}>
                  {row.map((v, i) => (
                    <td key={i} className={typeof v === "number" ? "num" : undefined}>
                      {scales.has(i) && typeof v === "number" ? (
                        <Bar value={v} scale={scales.get(i)!} />
                      ) : null}
                      {cell(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GridCard>
  );
}

function ReportTab({ report }: { report: ExternalReport }) {
  return (
    <section className="external-report" aria-label={report.title}>
      <h2>
        {report.title} <Freshness report={report} />
      </h2>
      <p className="muted">
        {report.source} · as of {report.asOf}
        {report.ageHours !== null && ` · ${report.ageHours.toFixed(1)} h ago`}
        {report.stale && ` · stale after ${String(report.staleAfterHours)} h`}
      </p>
      {report.summary !== null && <p>{report.summary}</p>}
      <div className="grid-12">
        {report.sections.map((s, i) => (
          <Section key={i} report={report} section={s} />
        ))}
      </div>
    </section>
  );
}

function InvalidTab({ bad }: { bad: ExternalReportInvalid }) {
  return (
    <section className="external-report">
      <h2>
        {bad.id} <span className="chip chip-bad">invalid</span>
      </h2>
      <p>This file does not meet the report contract, so none of it is drawn:</p>
      <ul>
        {bad.reasons.map((why, i) => (
          <li key={i}>{why}</li>
        ))}
      </ul>
      <p className="muted">
        Check it with <code>python run.py external-check data/external/{bad.id}.json</code>.
      </p>
    </section>
  );
}

function Overview({ reports, invalid }: { reports: ExternalReport[]; invalid: ExternalReportInvalid[] }) {
  if (reports.length === 0 && invalid.length === 0) {
    return (
      <div className="external-empty">
        <h2>External reports</h2>
        <p>
          Nothing here yet. A report is a small JSON file your own script writes to{" "}
          <code>data/external/&lt;id&gt;.json</code> in the suite home; this page draws each one.
        </p>
        <p className="muted">
          See <code>docs/guide/external-reports.md</code> for the format, an example script and how to schedule it.
        </p>
      </div>
    );
  }
  return (
    <table className="data-table external-overview">
      <thead>
        <tr>
          <th>Report</th>
          <th>Source</th>
          <th>As of</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        {reports.map((r) => (
          <tr key={r.id}>
            <td>
              <Link to={`${BASE}/${r.id}`}>{r.title}</Link>
            </td>
            <td>{r.source}</td>
            <td>{r.asOf}</td>
            <td>
              <Freshness report={r} />
            </td>
          </tr>
        ))}
        {invalid.map((r) => (
          <tr key={r.id}>
            <td>
              <Link to={`${BASE}/${r.id}`}>{r.id}</Link>
            </td>
            <td colSpan={2} className="muted">
              {r.reasons.join("; ")}
            </td>
            <td>
              <span className="chip chip-bad">invalid</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ExternalReportsPage() {
  const { data, isLoading, isError } = useExternalReports();
  const { reportId } = useParams();
  if (isLoading) return <p className="muted">Loading…</p>;
  if (isError || !data) return <p className="muted">External reports could not be read.</p>;
  const tabs = [
    ...data.reports.map((r) => ({ id: r.id, label: r.title, bad: false })),
    ...data.invalid.map((r) => ({ id: r.id, label: r.id, bad: true })),
  ];
  const report = reportId === undefined ? undefined : data.reports.find((r) => r.id === reportId);
  const bad = reportId === undefined ? undefined : data.invalid.find((r) => r.id === reportId);
  return (
    <div className="external-reports">
      <nav className="external-tabs" aria-label="external reports">
        <Link to={BASE} className="external-tab" aria-current={reportId === undefined ? "page" : undefined}>
          overview
        </Link>
        {tabs.map((t) => (
          <Link
            key={t.id}
            to={`${BASE}/${t.id}`}
            className={`external-tab${t.bad ? " external-tab-bad" : ""}`}
            aria-current={reportId === t.id ? "page" : undefined}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      {reportId === undefined ? (
        <Overview reports={data.reports} invalid={data.invalid} />
      ) : report !== undefined ? (
        <ReportTab report={report} />
      ) : bad !== undefined ? (
        <InvalidTab bad={bad} />
      ) : (
        <div className="external-missing">
          <h2>No such report</h2>
          <p>
            There is no report called <code>{reportId}</code> in <code>data/external/</code>. It may have been
            removed, or its script has not run yet.
          </p>
          <p>
            <Link to={BASE}>Back to the overview</Link>
          </p>
        </div>
      )}
    </div>
  );
}
