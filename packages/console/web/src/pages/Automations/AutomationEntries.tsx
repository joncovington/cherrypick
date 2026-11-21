import { Link } from "react-router-dom";
import type { AutomationEntries, AutomationsEntries, EntryFunnel, EntryStep } from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { useAutomationEntries, useAutomationsEntries } from "../../lib/api";
import { runLabel } from "../Builder/BuilderRunCard";
import { automationHref } from "./automationsModels";

/**
 * How often an automation enters, and where the sessions it did not enter stopped. Every figure is the
 * automations package's (`cli entries`): the session register cut into the steps of the engine's
 * decision, and the pinned run's reasons cut by the same steps beside it. The page only draws the
 * shares as bars and words a reason's spelling.
 */

const HELP: CardHelpContent = {
  title: "How often it enters",
  body: (
    <>
      <HelpSection title="Due">
        <p>
          A session the automation's weekdays asked it to trade, once its entry window has closed. A window still open today is pending and not
          counted.
        </p>
      </HelpSection>
      <HelpSection title="The steps">
        <p>
          The decision runs in order: its data, its entry firing, its limits and the event calendar, its market gates, finding and pricing its
          structure (with the gates that read the legs), and its size. A session that stopped at one step passed every step before it.
        </p>
      </HelpSection>
      <HelpSection title="The last try">
        <p>
          A session is counted where its last try in the window stopped. One that passed the gates at 10:00 and found no price at 10:04 counts
          at the structure, not the gates.
        </p>
      </HelpSection>
      <HelpSection title="The entries tab">
        <p>
          One row per automation: the sessions it was due, how many it entered, and after the rate, how many sessions it lost at each step
          (data, entry, limits, gates, structure, size).
        </p>
      </HelpSection>
      <HelpSection title="Beside the run">
        <p>
          The pinned run's sessions go through the same steps, by the driver's own last reason each session. A step where paper loses far more
          sessions than the run did is where paper and the backtest part. Missing data is often the feed or the loop, not the rule.
        </p>
      </HelpSection>
    </>
  ),
};

const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${String(Math.round(v * 100))}%`);

/** A reason in words: its kind before the colon, underscores as spaces. */
export function reasonWords(reason: string): string {
  const [head, ...rest] = reason.split(":");
  const words = (s: string) => s.replace(/_/g, " ");
  return rest.length ? `${words(head ?? "")}: ${words(rest.join(":"))}` : words(reason);
}

const STEP_WORDS: Record<EntryStep, string> = {
  due: "other",
  data: "data",
  trigger: "entry",
  limits: "limits",
  gates: "gates",
  structure: "structure",
  size: "size",
};

const OUTCOME_CHIP: Record<string, string> = { entered: "chip chip-ok", missed: "chip chip-warn", pending: "chip" };

function Bar({ share, kind }: { share: number | null; kind: "paper" | "run" }) {
  return (
    <span className="ae-bar">
      <span className={`ae-bar-fill is-${kind}`} style={{ width: `${String(Math.round((share ?? 0) * 100))}%` }} />
    </span>
  );
}

function FunnelRow({ label, count, share, runShare, run, end }: { label: string; count: number; share: number | null; runShare: number | null; run: boolean; end?: boolean }) {
  return (
    <div className={end ? "ae-funnel-row ae-funnel-end" : "ae-funnel-row"} role="row">
      <span role="cell" className="ae-funnel-label">
        {label}
      </span>
      <span role="cell" className="ae-funnel-cell">
        <Bar share={share} kind="paper" />
        <span className="ae-funnel-num">
          {count} · {pct(share)}
        </span>
      </span>
      {run && (
        <span role="cell" className="ae-funnel-cell">
          <Bar share={runShare} kind="run" />
          <span className="ae-funnel-num">{pct(runShare)}</span>
        </span>
      )}
    </div>
  );
}

function FunnelCard({ paper, run }: { paper: EntryFunnel; run: EntryFunnel | null }) {
  const runs = new Map((run?.steps ?? []).map((s) => [s.step, s]));
  // The `due` step holds only the reasons no step names; it is drawn only when it lost a session.
  const steps = paper.steps.filter((s) => s.step !== "due" || s.stopped > 0 || (runs.get("due")?.stopped ?? 0) > 0);
  const last = steps[steps.length - 1]?.step;
  return (
    <Card title="where the sessions went" collapseKey="automations-automation-funnel" className="lv-8" help={HELP}>
      <div className="ae-funnel" role="table" aria-label="Sessions past each step of the decision">
        <div className="ae-funnel-row ae-funnel-head" role="row">
          <span role="columnheader">step</span>
          <span role="columnheader">paper</span>
          {run && <span role="columnheader">the run</span>}
        </div>
        <FunnelRow label="Due" count={paper.due} share={paper.due ? 1 : null} runShare={run?.due ? 1 : null} run={run !== null} />
        {steps.map((s) => (
          <FunnelRow
            key={s.step}
            label={s.step === last ? `${s.label}: entered` : s.label}
            count={s.passed}
            share={s.passed_share}
            runShare={runs.get(s.step)?.passed_share ?? null}
            run={run !== null}
            end={s.step === last}
          />
        ))}
      </div>
      <p className="ar-legend">
        <span>Each bar is the share of due sessions past the step; what passes the last one entered.</span>
        {!run && <span>Pin a Builder run as this automation's reference to see the run's beside it.</span>}
      </p>
    </Card>
  );
}

export function EntriesBody({ name, data }: { name: string; data: AutomationEntries }) {
  if (data.error !== null || data.paper === null) {
    return (
      <Card title="how often it enters" collapseKey="automations-automation-funnel" className="lv-12" help={HELP}>
        <p className="error">{data.error ?? "No entries read."}</p>
      </Card>
    );
  }
  const paper = data.paper;
  const run = data.run;
  const loss = paper.most_lost;
  if (paper.due === 0) {
    return (
      <Card title="how often it enters" collapseKey="automations-automation-funnel" className="lv-12" help={HELP}>
        <p className="muted">
          No session has been due yet{paper.pending ? ` (${String(paper.pending)} window still open today)` : ""}. Its register starts with the
          first session its start date and weekdays allow.
        </p>
      </Card>
    );
  }
  return (
    <>
      <FliesTile
        title="entry rate"
        value={run ? `${pct(paper.rate)} vs ${pct(run.rate)}` : pct(paper.rate)}
        tone="dim"
        foot={`entered ${String(paper.entered)} of ${String(paper.due)} sessions due${run ? "; the run's share of its sessions beside it" : ""}`}
        help={HELP}
      />
      <FliesTile
        title="sessions due"
        value={String(paper.due)}
        tone="dim"
        foot={`${data.first ?? "?"} to ${data.last ?? "?"}${paper.pending ? `, ${String(paper.pending)} pending today` : ""}`}
      />
      <FliesTile
        title="most lost at"
        value={loss === null ? "none" : STEP_WORDS[loss.step]}
        tone={loss === null ? "pos" : "dim"}
        foot={loss === null ? "every due session entered" : `${String(loss.stopped)} sessions stopped there, ${pct(loss.share)} of those due`}
      />
      {data.withheld && (
        <FliesTile title="the run" value="withheld" tone="dim" foot="its pinned run is licensed, and licensed results are not shown now" />
      )}
      {data.reference && (
        <FliesTile
          title="the run"
          value={runLabel(data.reference.run)}
          tone="dim"
          foot={`${String(data.reference.sessions ?? "?")} sessions, ${data.reference.first ?? "?"} to ${data.reference.last ?? "?"}${data.reference.licensed ? ", licensed" : ""}`}
        />
      )}
      <FunnelCard paper={paper} run={run} />
      <DataCard
        title="why the rest did not enter"
        className="lv-4"
        headers={run ? ["cause", "step", "paper", "run"] : ["cause", "step", "paper"]}
        rowCount={data.causes.length}
        loading={false}
        numFrom={2}
        empty="every due session entered"
        help={HELP}
      >
        {data.causes.map((c) => (
          <tr key={c.reason}>
            <td>{reasonWords(c.reason)}</td>
            <td>{STEP_WORDS[c.step]}</td>
            <td>{pct(c.paper)}</td>
            {run && <td>{pct(c.run)}</td>}
          </tr>
        ))}
      </DataCard>
      <DataCard
        title="its latest sessions"
        className="lv-12"
        headers={["session", "outcome", "last reason", "step"]}
        rowCount={data.days.length}
        loading={false}
        empty="no session due yet"
        help={HELP}
      >
        {data.days.map((d) => (
          <tr key={d.session}>
            <td>{d.session}</td>
            <td>
              <span className={OUTCOME_CHIP[d.outcome] ?? "chip"}>{d.outcome}</span>
            </td>
            <td>{d.outcome === "entered" ? "—" : d.reason ? reasonWords(d.reason) : "—"}</td>
            <td>{d.step ? STEP_WORDS[d.step] : "—"}</td>
          </tr>
        ))}
      </DataCard>
      <p className="muted lv-12">
        <Link to={`/automations/entries#${encodeURIComponent(name)}`}>Every automation's entries ›</Link>
      </p>
    </>
  );
}

export function EntriesSection({ name }: { name: string }) {
  const q = useAutomationEntries(name);
  return (
    <Section id="automation-entries" concept="add" tag="entries" question="How often does it enter, and where do the other sessions stop?">
      {q.data === undefined ? (
        <Card title="how often it enters" collapseKey="automations-automation-funnel" className="lv-12" help={HELP}>
          <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read its entries." : "Reading its entries…"}</p>
        </Card>
      ) : (
        <EntriesBody name={name} data={q.data} />
      )}
    </Section>
  );
}

const STEPS: EntryStep[] = ["data", "trigger", "limits", "gates", "structure", "size"];

/** Automations › entries: every automation's funnel in one table, each linking to its page. */
export function EntriesTable({ data }: { data: AutomationsEntries | undefined }) {
  const rows = data?.automations ?? [];
  const stopped = (f: EntryFunnel, step: EntryStep) => f.steps.find((s) => s.step === step)?.stopped ?? 0;
  // A reason no step names is rare; its column shows only when some automation gave one.
  const steps: EntryStep[] = rows.some((r) => stopped(r.funnel, "due") > 0) ? [...STEPS, "due"] : STEPS;
  return (
    <DataCard
      title="every automation's entries"
      className="lv-12"
      headers={["automation", "group", "sessions", "due", "entered", "rate", ...steps.map((s) => STEP_WORDS[s])]}
      rowCount={rows.length}
      loading={data === undefined}
      numFrom={3}
      empty={data?.error ?? "no automation configured"}
      help={HELP}
    >
      {rows.map((r) => (
        <tr key={r.name} id={r.name}>
          <td>
            {r.configured ? <Link to={automationHref(r.name)}>{r.name}</Link> : r.name}
            {!r.enabled && r.configured && <span className="chip ae-off">off</span>}
            {!r.configured && <span className="chip ae-off">removed</span>}
          </td>
          <td>{r.group ?? "—"}</td>
          <td>{r.first ? `${r.first} to ${r.last ?? "?"}` : "—"}</td>
          <td>{r.funnel.due}</td>
          <td>{r.funnel.entered}</td>
          <td>{pct(r.funnel.rate)}</td>
          {steps.map((s) => (
            <td key={s}>{stopped(r.funnel, s) || "—"}</td>
          ))}
        </tr>
      ))}
    </DataCard>
  );
}

export function AutomationsEntriesPage() {
  const q = useAutomationsEntries();
  return (
    <div className="pf-layout">
      <Section concept="add" tag="entries" question="How often does each automation enter, and where do the other sessions stop?">
        <EntriesTable data={q.data} />
      </Section>
    </div>
  );
}
