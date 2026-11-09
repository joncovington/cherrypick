import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  ExternalColumn,
  ExternalColumnObject,
  ExternalCell,
  ExternalKeyEntry,
  ExternalReport,
  ExternalReportInvalid,
  ExternalRow,
  ExternalSection,
  ExternalTone,
} from "@console/shared";
import { GridCard } from "../../components/grid/GridCard";
import { cellParts, cellText, cellTone, columnSpec, rowCells, type ColumnSpec } from "../../lib/externalFormat";
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
 *
 * A table may carry presentation hints (formats, tones, stacked bars, a key, a footnote); every one is
 * optional and every figure stays in its cell as text, the picture beside it never replacing it. A
 * tone is a theme token (`external-tone-*` in styles.css), so it reads in light and dark.
 */

const BASE = "/reports/external";

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
  /** A fixed scale's minimum (the report's `bar.scale`); absent when the column's own maximum rules. */
  min?: number;
  /** A grey track under the bar. */
  track?: boolean;
  /** The bar has its own column after the number. */
  own?: boolean;
}

const valueAt = (row: ExternalRow, col: number): number | null => {
  const v = cellParts(rowCells(row)[col] as ExternalCell).value;
  return typeof v === "number" ? v : null;
};

/** The scale of each column the table marks with `bars`, within that column only. A null or text cell
 *  has no bar; a column with nothing to scale (all null, or all zero) gets none. */
export function barScales(table: {
  rows: ExternalRow[];
  bars?: number[] | undefined;
  columns?: ExternalColumn[] | undefined;
}): Map<number, BarScale> {
  const out = new Map<number, BarScale>();
  const wanted = new Map<number, NonNullable<ExternalColumnObject["bar"]>>();
  for (const col of table.bars ?? []) wanted.set(col, {});
  (table.columns ?? []).forEach((c, i) => {
    if (typeof c !== "string" && c.bar !== undefined) wanted.set(i, c.bar);
  });
  for (const [col, cfg] of wanted) {
    const extras = { ...(cfg.track === true ? { track: true } : {}), ...(cfg.own_column === true ? { own: true } : {}) };
    if (cfg.scale !== undefined) {
      const [min, max] = cfg.scale;
      out.set(col, { max, min, signed: min < 0, ...extras });
      continue;
    }
    const values = table.rows.map((r) => valueAt(r, col)).filter((v): v is number => v !== null);
    const max = Math.max(0, ...values.map((v) => Math.abs(v)));
    if (max > 0) out.set(col, { max, signed: values.some((v) => v < 0), ...extras });
  }
  return out;
}

/** Share of its side of the track a value fills, 0 to 100: against the column's largest magnitude, or
 *  against the fixed scale's own end on that side (`min` for a negative, `max` for a positive). */
export function barPercent(value: number, scale: BarScale): number {
  const end = scale.min !== undefined && value < 0 ? Math.abs(scale.min) : scale.max;
  return end > 0 ? Math.min(100, (Math.abs(value) / end) * 100) : 0;
}

/** An inline bar beside the number, which stays in the cell as text: the bar is a picture of it. */
function Bar({ value, scale }: { value: number; scale: BarScale }) {
  const pct = barPercent(value, scale);
  const negative = value < 0;
  // A fixed scale that is not symmetric puts the zero line off the middle, in proportion.
  const split = scale.min !== undefined && scale.signed ? -scale.min / (scale.max - scale.min) : 0.5;
  const sized = (flex: number) => (split === 0.5 ? undefined : { flex });
  return (
    <span
      className={`external-bar${scale.signed ? " external-bar-signed" : ""}${scale.track === true ? " external-bar-track" : ""}`}
      aria-hidden="true"
    >
      {scale.signed && (
        <span className="external-bar-half external-bar-left" style={sized(split)}>
          {negative && <span className="external-bar-fill external-bar-neg" style={{ width: `${String(pct)}%` }} />}
        </span>
      )}
      <span className="external-bar-half external-bar-right" style={sized(1 - split)}>
        {!negative && <span className="external-bar-fill external-bar-pos" style={{ width: `${String(pct)}%` }} />}
      </span>
    </span>
  );
}

/** A segment narrower than this many pixels is widened to it, so a sliver that has trades in it is
 *  still visible; the rest shrink to fit. */
const MIN_SEGMENT = 3;
const STACK_WIDTH = 160;

/** Segment widths in pixels for `values` across `width`: in proportion, every non-zero one at least
 *  `MIN_SEGMENT`, the roomy ones giving up the excess. Empty when there is nothing to draw. */
export function stackWidths(values: (number | null)[], width: number = STACK_WIDTH): number[] {
  const nums = values.map((v) => (v !== null && v > 0 ? v : 0));
  const total = nums.reduce((a, v) => a + v, 0);
  if (total <= 0) return [];
  const floored = nums.map((v) => (v > 0 ? Math.max((v / total) * width, MIN_SEGMENT) : 0));
  const excess = floored.reduce((a, w) => a + w, 0) - width;
  const roomy = floored.reduce((a, w) => a + (w > MIN_SEGMENT ? w : 0), 0);
  return floored.map((w) => (w > MIN_SEGMENT && roomy > 0 ? w - (excess * w) / roomy : w));
}

/** The legend of a stacked column, in the header: a swatch and a label per segment. */
function StackLegend({ labels }: { labels: string[] }) {
  return (
    <span className="external-legend">
      {labels.map((l, i) => (
        <span key={i}>
          <span className={`external-swatch external-stack-${String(i + 1)}`} aria-hidden="true" /> {l}
        </span>
      ))}
    </span>
  );
}

/** One horizontal bar split in proportion. The figures stay as text, hidden visually but not from a
 *  reader of the page, and as the tooltip. */
function StackBar({ values, labels }: { values: (number | null)[]; labels: string[] }) {
  const widths = stackWidths(values);
  if (widths.length === 0) return <>{"—"}</>;
  const total = values.reduce<number>((a, v) => a + (v !== null && v > 0 ? v : 0), 0);
  const said = values.map((v, i) => `${labels[i] ?? ""} ${String(Math.round(((v ?? 0) / total) * 1000) / 10)}%`).join(" · ");
  return (
    <span className="external-stack" role="img" aria-label={said} title={said}>
      {widths.map((w, i) =>
        w > 0 ? <span key={i} className={`external-stack-seg external-stack-${String(i + 1)}`} style={{ width: `${String(w)}px` }} /> : null,
      )}
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

const toneClass = (tone: ExternalTone | null): string => (tone === null ? "" : ` external-tone-${tone}`);

function Cell({
  col,
  cell,
  bar,
}: {
  col: ColumnSpec;
  cell: ExternalCell;
  bar: BarScale | undefined;
}) {
  if (col.stack !== null) {
    return Array.isArray(cell) ? <StackBar values={cell} labels={col.stack} /> : <>{"—"}</>;
  }
  const parts = cellParts(cell);
  const text = cellText(col, parts);
  return (
    <>
      {parts.marker !== null && (
        <span className={`external-marker external-marker-${parts.marker}${toneClass(parts.tone)}`} aria-hidden="true" />
      )}
      {text}
      {parts.suffix !== null && (
        <span className={`external-suffix${toneClass(parts.suffix.tone ?? "muted")}`}> {parts.suffix.text}</span>
      )}
      {bar !== undefined && typeof parts.value === "number" ? <Bar value={parts.value} scale={bar} /> : null}
    </>
  );
}

function Key({ entries }: { entries: ExternalKeyEntry[] }) {
  return (
    <dl className="external-key">
      {entries.map((e, i) => (
        <div key={i}>
          <dt>
            <span className={`external-key-tag${toneClass(e.tone ?? null)}`}>{e.tag}</span>
            {e.label !== undefined ? ` ${e.label}` : ""}
          </dt>
          <dd>{e.text}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ report, section }: { report: ExternalReport; section: ExternalSection }) {
  // The card's mark, which a capture waits for, rides on the foot; with no foot a hidden one stays.
  const foot =
    report.showSourceFoot === false ? undefined : (
      <>
        <Glyph /> {report.source}, as of {report.asOf}
      </>
    );
  const mark = foot === undefined ? <svg className="external-mark" width="0" height="0" aria-hidden="true" /> : null;
  if ("text" in section) {
    return (
      <GridCard label={section.heading} span={12} h={96} className="external-card" foot={foot}>
        {mark}
        <p className="external-text">{section.text}</p>
      </GridCard>
    );
  }
  const { rows } = section.table;
  const cols = section.table.columns.map(columnSpec);
  const scales = barScales(section.table);
  const numbered = section.numbered === true;
  const headClass = (c: ColumnSpec) =>
    `${c.align === "right" ? "external-right" : ""}${c.width !== null ? ` external-${c.width}` : ""}`.trim() || undefined;
  return (
    <GridCard label={section.heading} span={12} h={96} className="external-card" foot={foot}>
      {mark}
      {rows.length === 0 ? (
        <p className="muted">No rows.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table external-table">
            <thead>
              <tr>
                {numbered && <th className="external-right external-narrow">#</th>}
                {cols.map((c, i) => (
                  <Fragment key={i}>
                    <th className={headClass(c)}>
                      {c.label}
                      {c.stack !== null && <StackLegend labels={c.stack} />}
                    </th>
                    {scales.get(i)?.own === true && <th className="external-barcol" />}
                  </Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => {
                const emphasis = Array.isArray(row) ? undefined : row.emphasis;
                return (
                  <tr key={r} className={emphasis === undefined ? undefined : `external-row-${emphasis}`}>
                    {numbered && <td className="external-right external-index">{r + 1}</td>}
                    {rowCells(row).map((v, i) => {
                      const col = cols[i]!;
                      const parts = Array.isArray(v) ? null : cellParts(v);
                      const tone = parts === null ? null : cellTone(col, parts);
                      // A plain number keeps the right edge it always had, whatever the column says.
                      const right = col.align === "right" || (col.format === "text" && typeof parts?.value === "number");
                      const classes = [right ? "external-right" : "", tone === null ? "" : `external-tone-${tone}`];
                      const scale = scales.get(i);
                      const own = scale?.own === true;
                      const value = parts?.value;
                      return (
                        <Fragment key={i}>
                          <td className={classes.filter((x) => x !== "").join(" ") || undefined}>
                            <Cell col={col} cell={v} bar={own ? undefined : scale} />
                          </td>
                          {own && (
                            <td className="external-barcol">
                              {scale !== undefined && typeof value === "number" ? <Bar value={value} scale={scale} /> : null}
                            </td>
                          )}
                        </Fragment>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {section.key !== undefined && section.key.length > 0 && <Key entries={section.key} />}
      {section.footnote !== undefined && <p className="external-footnote">{section.footnote}</p>}
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
