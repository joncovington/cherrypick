import { Link } from "react-router-dom";
import type { AutomationReading, ReadingPlace, ReferenceBandPoint } from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { useAutomationReading } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { runLabel } from "../Builder/BuilderRunCard";

/**
 * "Is it working?" on one automation's page: its paper record set against its pinned Builder run. Every
 * figure is the backtest package's (`read automation-reading`): each trade's place in the run's usual
 * range (10th to 90th percentile of a trade's net) and its percentile, the running net against the run's
 * band of paths, the sample (the forward rule's `min_obs`, else computed from the run) and the entry
 * rates. The page only scales them onto a strip and a chart. A few weeks of paper is a forward read,
 * never a verdict, and the reading says where the record sits, not whether the rule is good.
 */

const HELP: CardHelpContent = {
  title: "Is it working?",
  body: (
    <>
      <HelpSection title="The yardstick">
        <p>
          The run pinned as this automation's backtest reference (Builder, Use as reference). Paper is set against that run only, so the yardstick
          moves only when another run is pinned.
        </p>
      </HelpSection>
      <HelpSection title="The usual range">
        <p>
          The 10th to the 90th percentile of a trade's net in the run, after fees and slippage. One trade in five falls outside it by chance; a
          credit structure's rare large loss sits far below it and is still what the run expects.
        </p>
      </HelpSection>
      <HelpSection title="The band">
        <p>
          Eight in ten of the run's resampled paths of running net stay inside it (a block bootstrap of the run's own trades, seeded). The reading
          is where paper's running net sits in the band at its own trade count: inside, below or above. Under five trades it says too few.
        </p>
      </HelpSection>
      <HelpSection title="The sample">
        <p>
          How many trades a reading needs: the number the automation's forward rule declares, or, without one, the count the run's own spread
          needs to tell its mean from zero. Until then the reading is a forward read, not a verdict.
        </p>
      </HelpSection>
    </>
  ),
};

const STATE_WORDS: Record<string, string> = {
  inside: "on track",
  below: "below its band",
  above: "above its band",
  too_few: "too few to read",
  no_trades: "no trades yet",
  no_reference: "no reference",
};
const PLACE_CHIP: Record<ReadingPlace, string> = { inside: "chip chip-ok", below: "chip chip-warn", above: "chip chip-info" };
const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${String(Math.round(v * 100))}%`);

/** Where `v` sits on [lo, hi], as a percent of the width (clamped). */
export function at(v: number, lo: number, hi: number): number {
  return hi === lo ? 50 : Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
}

/** The strip's domain: the usual range with room either side, widened to every paper trade. */
export function stripDomain(p10: number, p90: number, nets: number[]): [number, number] {
  const pad = Math.max((p90 - p10) * 0.5, 1);
  return [Math.min(p10 - pad, ...nets), Math.max(p90 + pad, ...nets)];
}

function Strip({ p, rows }: { p: Record<string, number>; rows: { net: number; place: ReadingPlace }[] }) {
  const p10 = p["p10"] ?? 0;
  const p90 = p["p90"] ?? 0;
  const [lo, hi] = stripDomain(p10, p90, rows.map((r) => r.net));
  // Only the usual range is labelled: the median is in the legend, and the strip's own ends would crowd it.
  const ticks = [p10, p90];
  return (
    <div className="ar-strip" role="img" aria-label={`${String(rows.length)} paper trades on the run's usual range`}>
      <div className="ar-strip-track">
        <span className="ar-strip-band" style={{ left: `${at(p10, lo, hi)}%`, width: `${at(p90, lo, hi) - at(p10, lo, hi)}%` }} />
        <span className="ar-strip-iqr" style={{ left: `${at(p["p25"] ?? 0, lo, hi)}%`, width: `${at(p["p75"] ?? 0, lo, hi) - at(p["p25"] ?? 0, lo, hi)}%` }} />
        <span className="ar-strip-med" style={{ left: `${at(p["p50"] ?? 0, lo, hi)}%` }} />
        {rows.map((r, i) => (
          <span key={i} className={`ar-strip-dot is-${r.place}`} style={{ left: `${at(r.net, lo, hi)}%` }} />
        ))}
      </div>
      <div className="ar-strip-ticks">
        {ticks.map((t, i) => (
          <span key={i} style={{ left: `${at(t, lo, hi)}%` }}>
            {fmtMoney(t)}
          </span>
        ))}
      </div>
    </div>
  );
}

/** The run's band of running-net paths, its middle path, and paper's running net, in one SVG. */
export function bandChart(band: ReferenceBandPoint[], running: number[], w = 600, h = 220) {
  const K = band.length;
  const ys = [0, ...band.flatMap((b) => [b.p10, b.p90]), ...running];
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const x = (k: number) => 40 + (k / Math.max(K, 1)) * (w - 56);
  const y = (v: number) => (hi === lo ? h / 2 : 12 + (1 - (v - lo) / (hi - lo)) * (h - 40));
  const upper = [`${x(0)},${y(0)}`, ...band.map((b) => `${x(b.k)},${y(b.p90)}`)];
  const lower = [...band.map((b) => `${x(b.k)},${y(b.p10)}`).reverse(), `${x(0)},${y(0)}`];
  return {
    band: [...upper, ...lower].join(" "),
    middle: [`${x(0)},${y(0)}`, ...band.map((b) => `${x(b.k)},${y(b.p50)}`)].join(" "),
    paper: [`${x(0)},${y(0)}`, ...running.map((v, i) => `${x(i + 1)},${y(v)}`)].join(" "),
    zero: y(0),
    today: running.length ? x(running.length) : null,
    last: running.length ? { x: x(running.length), y: y(running[running.length - 1] ?? 0) } : null,
    lo,
    hi,
    K,
  };
}

function BandChart({ band, running }: { band: ReferenceBandPoint[]; running: number[] }) {
  const c = bandChart(band, running);
  return (
    <svg viewBox="0 0 600 220" className="ar-band" role="img" aria-label="Paper's running net against the run's band of paths">
      <line x1="40" x2="584" y1={c.zero} y2={c.zero} className="ar-band-zero" />
      <polygon points={c.band} className="ar-band-area" />
      <polyline points={c.middle} className="ar-band-mid" />
      {c.today !== null && <line x1={c.today} x2={c.today} y1="8" y2="200" className="ar-band-today" />}
      {running.length > 0 && <polyline points={c.paper} className="ar-band-paper" />}
      {c.last && <circle cx={c.last.x} cy={c.last.y} r="4" className="ar-band-dot" />}
      <text x="34" y={c.zero + 4} textAnchor="end" className="ar-band-label">
        $0
      </text>
      <text x="40" y="214" className="ar-band-label">
        0
      </text>
      <text x="584" y="214" textAnchor="end" className="ar-band-label">
        {c.K} trades
      </text>
    </svg>
  );
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card title={title} collapseKey="automations-automation-working" className="lv-12" help={HELP}>
      {children}
    </Card>
  );
}

export function WorkingBody({ name, data }: { name: string; data: AutomationReading }) {
  const builder = `/automations/builder?automation=${encodeURIComponent(name)}`;
  if (data.error !== null) return <Empty title="is it working?">{<p className="error">{data.error}</p>}</Empty>;
  if (data.pinned === null) {
    return (
      <Empty title="no backtest reference">
        <p className="muted">
          No Builder run is pinned as this automation's backtest reference, so its paper has nothing to be set against. Pin one of its explore
          runs in the Builder (Use as reference).
        </p>
        <Link className="btn" to={builder}>
          Open in Builder ›
        </Link>
      </Empty>
    );
  }
  if (data.withheld) {
    return (
      <Empty title="reference withheld">
        <p className="muted">Its reference run, {runLabel(data.pinned)}, is licensed, and licensed results are not shown now.</p>
      </Empty>
    );
  }
  const r = data.reading;
  if (r === null) return <Empty title="is it working?">{<p className="muted">Reading the reference…</p>}</Empty>;
  const ref = r.reference;
  if (r.state === "no_reference") {
    return (
      <Empty title="reference too short">
        <p className="muted">
          The pinned run, {runLabel(data.pinned)}, has {ref.trades} trades; a reference needs {ref.min_trades ?? 30}. Pin a longer run.
        </p>
        <Link className="btn" to={builder}>
          Open in Builder ›
        </Link>
      </Empty>
    );
  }
  const paper = r.paper;
  const rows = paper.rows ?? [];
  const sample = r.sample;
  const tone = r.state === "inside" ? "pos" : r.state === "below" ? "neg" : "dim";
  const counts = paper.counts ?? { below: 0, inside: 0, above: 0 };
  const needed = sample?.needed ?? null;
  return (
    <>
      <FliesTile
        title="reading"
        value={STATE_WORDS[r.state] ?? r.state}
        tone={tone}
        foot={
          rows.length === 0
            ? "paper has closed no trade yet"
            : `${String(counts.inside)} of ${String(rows.length)} trades inside the usual range, ${String(counts.below)} below, ${String(counts.above)} above`
        }
        help={HELP}
      />
      <FliesTile
        title="sample"
        value={needed === null ? String(sample?.have ?? 0) : `${String(sample?.have ?? 0)} of ${String(needed)}`}
        tone="dim"
        foot={
          needed === null
            ? "the run's mean is not above zero, so no sample is computed"
            : sample?.basis === "declared"
              ? "trades its forward rule declares; until then a forward read, not a verdict"
              : "trades the run's spread needs, computed; until then a forward read, not a verdict"
        }
      />
      <FliesTile
        title="entry rate"
        value={`${pct(paper.entry.rate)} vs ${pct(ref.entry_rate)}`}
        tone="dim"
        foot={`paper entered ${String(paper.entry.entered)} of ${String(paper.entry.entered + paper.entry.missed)} days due; the run's share of its sessions`}
      />
      <FliesTile
        title="mean trade"
        value={`${paper.mean === null || paper.mean === undefined ? "—" : fmtMoney(paper.mean)} vs ${fmtMoney(ref.mean ?? null)}`}
        tone="dim"
        foot="paper's net per trade beside the run's, both after fees and slippage"
      />
      {ref.percentiles && (
        <Card title="each trade on the usual range" collapseKey="automations-automation-strip" className="lv-12" help={HELP}>
          <Strip p={ref.percentiles} rows={rows} />
          <p className="ar-legend">
            <span className="ar-legend-item">
              <span className="ar-key ar-key-band" />
              the run's usual range, 10th to 90th percentile
            </span>
            <span className="ar-legend-item">
              <span className="ar-key ar-key-iqr" />
              its middle half
            </span>
            <span className="ar-legend-item">
              <span className="ar-key ar-key-med" />
              median {fmtMoney(ref.percentiles["p50"] ?? null)}
            </span>
            <span className="ar-legend-item">
              <span className="ar-key ar-key-dot" />a paper trade
            </span>
          </p>
        </Card>
      )}
      {ref.band && (
        <Card title="running net against the run's paths" collapseKey="automations-automation-band" className="lv-8" help={HELP}>
          <BandChart band={ref.band} running={rows.map((x) => x.running)} />
          <p className="ar-legend">
            <span className="ar-legend-item">
              <span className="ar-key ar-key-band" />8 in 10 of the run's paths
            </span>
            <span className="ar-legend-item">
              <span className="ar-key ar-key-mid" />
              its middle path
            </span>
            <span className="ar-legend-item">
              <span className="ar-key ar-key-paper" />
              paper
            </span>
            <span>past the dotted line, where the next trades should land</span>
          </p>
        </Card>
      )}
      <Card title="the pinned run" collapseKey="automations-automation-pinned" className="lv-4" help={HELP}>
        <dl className="ar-facts">
          <dt>run</dt>
          <dd>{runLabel(data.pinned)}</dd>
          <dt>span</dt>
          <dd>
            {ref.first ?? "?"} to {ref.last ?? "?"}
          </dd>
          <dt>trades</dt>
          <dd>{ref.trades}</dd>
          <dt>data</dt>
          <dd>{r.licensed ? <span className="chip">licensed</span> : "our own"}</dd>
        </dl>
        <Link className="btn" to={builder}>
          View in Builder ›
        </Link>
      </Card>
      {rows.length > 0 && (
        <DataCard
          title="each paper trade against the run"
          className="lv-12"
          headers={["#", "closed", "net", "percentile in the run", "place"]}
          rowCount={rows.length}
          loading={false}
          numFrom={2}
          empty="no trade closed yet"
          help={HELP}
        >
          {[...rows].reverse().map((x) => (
            <tr key={x.i}>
              <td>{x.i}</td>
              <td>{x.session ?? "—"}</td>
              <td className={x.net > 0 ? "pnl-pos" : x.net < 0 ? "pnl-neg" : undefined}>{fmtMoney(x.net)}</td>
              <td>{x.percentile.toFixed(1)}</td>
              <td>
                <span className={PLACE_CHIP[x.place]}>{x.place}</span>
              </td>
            </tr>
          ))}
        </DataCard>
      )}
    </>
  );
}

export function WorkingSection({ name }: { name: string }) {
  const q = useAutomationReading(name);
  return (
    <Section id="automation-working" concept="edge" tag="is it working?" question="Is it trading the way its backtest said it would?">
      {q.isLoading || q.data === undefined ? (
        q.isError ? (
          <Empty title="is it working?">
            <p className="error">Could not read the reference.</p>
          </Empty>
        ) : (
          <Empty title="is it working?">
            <p className="muted">Reading the reference…</p>
          </Empty>
        )
      ) : (
        <WorkingBody name={name} data={q.data} />
      )}
    </Section>
  );
}
