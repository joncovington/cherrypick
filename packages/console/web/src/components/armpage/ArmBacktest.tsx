import type { ArmBacktestReading } from "@console/shared";
import { Card } from "../DataTable";
import { HelpSection, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { FliesTile } from "../../pages/Flies/FliesSession";
import { BandChart, STATE_WORDS, Strip } from "../../pages/Automations/AutomationWorking";
import { useArmReading } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * A module arm against its backtest: the stored run whose arm has this arm's settings, whatever the study
 * called it (`backtest read arm-reading`). Read per session, since a module arm can enter many times a
 * session: each paper session's net on the run's usual range, and the running net inside the run's band.
 * Every figure is the backtest package's; the page only scales them. An arm no run has tried says so,
 * with no stand-in.
 */

const HELP: CardHelpContent = {
  title: "Against its backtest",
  body: (
    <>
      <HelpSection title="Which run">
        <p>
          The stored replay run whose arm has this arm&apos;s settings now: every parameter, the cadence and the fill model, whatever the study
          named it. Where several match, the one with the most sessions answers. An arm whose settings no run has tried has no reference, and
          nothing stands in for one.
        </p>
      </HelpSection>
      <HelpSection title="Per session">
        <p>
          An arm can enter many times a session, so both sides are read per session: the usual range is the 10th to 90th percentile of a session&apos;s
          net in the run, and the band is where eight in ten of the run&apos;s resampled paths of running net stay. Paper starts at the module&apos;s
          current measurement era.
        </p>
      </HelpSection>
    </>
  ),
};

function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card title={title} collapseKey="arm-backtest" className="lv-12" help={HELP}>
      {children}
    </Card>
  );
}

export function ArmBacktestBody({ data }: { data: ArmBacktestReading }) {
  const c = data.chosen;
  const runLine = c ? `${c.run} (called ${c.run_arm} there, ${c.window} window, ${c.first ?? "?"} to ${c.last ?? "?"}, ${String(c.sessions)} sessions)` : "";
  if (data.error !== null) return <Note title="against its backtest">{<p className="error">{data.error}</p>}</Note>;
  if (c === null) {
    return (
      <Note title="no matching backtest">
        <p className="muted">
          No stored run has tried {data.arm}&apos;s settings as they are now, so there is nothing to read it against. A reference appears once a replay
          runs an arm with these settings.
        </p>
      </Note>
    );
  }
  if (data.withheld) {
    return (
      <Note title="backtest withheld">
        <p className="muted">Its run, {runLine}, read licensed data, and licensed results are not shown now.</p>
      </Note>
    );
  }
  const r = data.reading;
  if (r === null) return <Note title="against its backtest">{<p className="muted">Reading the run…</p>}</Note>;
  const ref = r.reference;
  if (r.state === "no_reference") {
    return (
      <Note title="run too short">
        <p className="muted">
          Its run, {runLine}, has {ref.trades} sessions; a reference needs {ref.min_trades ?? 30}.
        </p>
      </Note>
    );
  }
  const rows = r.paper.rows ?? [];
  const counts = r.paper.counts ?? { below: 0, inside: 0, above: 0 };
  const needed = r.sample?.needed ?? null;
  return (
    <>
      <FliesTile
        title="reading"
        value={STATE_WORDS[r.state] ?? r.state}
        tone={r.state === "inside" ? "pos" : r.state === "below" ? "neg" : "dim"}
        foot={rows.length === 0 ? "no paper session yet" : `${String(counts.inside)} of ${String(rows.length)} sessions inside the usual range`}
        help={HELP}
      />
      <FliesTile
        title="sessions"
        value={needed === null ? String(r.sample?.have ?? 0) : `${String(r.sample?.have ?? 0)} of ${String(needed)}`}
        tone="dim"
        foot={needed === null ? "the run's mean is not above zero, so no sample is computed" : "the run's spread needs this many; until then a forward read"}
      />
      <FliesTile
        title="mean session"
        value={`${r.paper.mean === null || r.paper.mean === undefined ? "—" : fmtMoney(r.paper.mean)} vs ${fmtMoney(ref.mean ?? null)}`}
        tone="dim"
        foot={`paper since ${data.paper_from ?? "its era"}, beside the run`}
      />
      {ref.percentiles && (
        <Card title="each paper session on the run's usual range" collapseKey="arm-backtest-strip" className="lv-12" help={HELP}>
          <Strip p={ref.percentiles} rows={rows} />
        </Card>
      )}
      {ref.band && (
        <Card title="running net against the run's paths" collapseKey="arm-backtest-band" className="lv-8" help={HELP}>
          <BandChart band={ref.band} running={rows.map((x) => x.running)} unit="sessions" />
        </Card>
      )}
      <Card title="the run" collapseKey="arm-backtest-run" className="lv-4" help={HELP}>
        <dl className="ar-facts">
          <dt>run</dt>
          <dd>{c.run}</dd>
          <dt>its arm</dt>
          <dd>{c.run_arm}</dd>
          <dt>window</dt>
          <dd>
            {c.window}, {c.first ?? "?"} to {c.last ?? "?"}
          </dd>
          <dt>sessions</dt>
          <dd>{c.sessions}</dd>
          <dt>data</dt>
          <dd>
            <span className="chip">licensed</span>
          </dd>
          {data.matches.length > 1 && (
            <>
              <dt>also matching</dt>
              <dd>{data.matches.slice(1).map((m) => `${m.run} (${m.window})`).join(", ")}</dd>
            </>
          )}
        </dl>
      </Card>
    </>
  );
}

export function ArmBacktestSection({ module, arm, id }: { module: "meic"; arm: string; id?: string }) {
  const q = useArmReading(module, arm);
  return (
    <Section id={id} concept="edge" tag="vs its backtest" question="Is it trading the way its backtest did?">
      {q.data === undefined ? (
        <Note title="against its backtest">
          <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read its backtest." : "Reading its backtest…"}</p>
        </Note>
      ) : (
        <ArmBacktestBody data={q.data} />
      )}
    </Section>
  );
}
