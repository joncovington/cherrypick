import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { GroupsEditMember, GroupsEditView } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { builderDraft, useAutomations, useAutomationsBuilder, useAutomationsPerformance, useBuilderSchema, useGroupsEdit } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { blockWords } from "../Builder/specWords";
import { TradeShape } from "../Builder/TradeShape";
import { ClosedStructures, OpenTable } from "./AutomationsPages";
import { WorkingSection } from "./AutomationWorking";
import { EntriesSection } from "./AutomationEntries";
import { ChangesSection } from "../../components/changes/ChangesSlide";
import { SessionsAheadSection } from "../../components/armpage/SessionsAhead";
import { RetiredSection } from "./RetiredSection";

/**
 * One automation's page (`/automations/automation/<name>`): everything about one automation in one
 * place, in the order every arm page follows: what it does, whether it is working against its pinned
 * backtest reference, then what it has done, then what it decided, and at its foot whether it is retired.
 * Its one write is that last switch (`RetiredSection`); its switches and its group are changed in Groups (the
 * drill-in this page links to, where changes are staged and saved), and its rule in the Builder.
 *
 * In the automations ledger an arm is the automation's name, so its record is the module's own rows
 * filtered to that name, read through the same endpoints as the module's tabs.
 */

const HELP: CardHelpContent = {
  title: "One automation",
  body: (
    <>
      <HelpSection title="What this page is">
        <p>
          Everything about one automation: its rule in plain words, whether it is switched on, what it has done on paper and what it decided
          this session. Change its switches in Groups and its rule in the Builder; the one change made here is retiring it, at the foot of the page.
        </p>
      </HelpSection>
      <HelpSection title="A forward read">
        <p>A few weeks of paper is a forward read, not a verdict. The research verdict is the register's label on the automation, from its backtest.</p>
      </HelpSection>
    </>
  ),
};

const VERDICT_WORDS: Record<string, string> = { passed: "passed", failed: "failed", no_verdict: "no verdict" };
const VERDICT_CHIP: Record<string, string> = { passed: "chip chip-ok", failed: "chip chip-warn", no_verdict: "chip" };
const STATE_WORDS: Record<string, string> = { on: "on", waiting: "waiting on its start", off_group: "off · its group", off_own: "off · its own switch", retired: "retired" };
const STATE_CHIP: Record<string, string> = { on: "chip chip-ok", waiting: "chip chip-info", off_group: "chip chip-warn", off_own: "chip", retired: "chip" };

/** The automation and the group it is in, from the module's groups view; undefined when no automation has that name. */
export function findAutomation(view: GroupsEditView, name: string): { m: GroupsEditMember; group: string | null } | undefined {
  for (const g of view.groups) {
    const m = g.members.find((x) => x.name === name);
    if (m !== undefined) return { m, group: g.name };
  }
  const m = view.ungrouped.find((x) => x.name === name);
  return m === undefined ? undefined : { m, group: null };
}

/** The switches drill-in in Groups for this automation. */
function switchesHref(name: string, group: string | null): string {
  return `/automations/groups?group=${encodeURIComponent(group ?? "_none")}&automation=${encodeURIComponent(name)}`;
}

/** The rule in plain words, the module's own sentences for its saved spec, with each block's backtest label. */
export function RuleCard({ name, help }: { name: string; help: CardHelpContent }) {
  const schema = useBuilderSchema();
  const builder = useAutomationsBuilder();
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let live = true;
    void builderDraft(name).then((r) => {
      if (live && r.ok && r.automation) setSpec(r.automation as Record<string, unknown>);
    });
    return () => {
      live = false;
    };
  }, [name]);
  const blocks = builder.data?.automations.find((a) => a.name === name)?.blocks ?? [];
  const words = spec && schema.data?.schema?.words ? blockWords(schema.data.schema.words, schema.data.schema, spec) : null;
  return (
    <Card
      title="in plain English"
      collapseKey="automations-automation-rule"
      className="lv-8"
      help={help}
      controls={
        <span className="grp-cell">
          <Link className="btn" to={`/automations/builder?automation=${encodeURIComponent(name)}`}>
            View in Builder
          </Link>
        </span>
      }
    >
      {words === null ? (
        <p className="muted">Reading the rule…</p>
      ) : (
        <div className="grp-rule">
          {(["structure", "entry", "gates", "exit", "size"] as const).map((k) => (
            <p key={k}>
              <span className="builder-lbl">{k}</span> {words[k]}
            </p>
          ))}
        </div>
      )}
      <span className="grp-cell">
        {blocks.map((b) => (
          <span key={b.block} className={b.state === "backtested" ? "chip chip-ok" : b.state === "changed" ? "chip chip-warn" : "chip"}>
            {b.block} · {b.state === "backtested" ? "backtested" : b.state === "changed" ? "changed since its backtest" : "not backtested"}
          </span>
        ))}
      </span>
    </Card>
  );
}

const JUMPS = [
  { id: "automation-rule", label: "The rule" },
  { id: "automation-working", label: "Is it working?" },
  { id: "automation-record", label: "What it did" },
  { id: "automation-entries", label: "Entries" },
  { id: "automation-ahead", label: "Sessions ahead" },
  { id: "automation-changes", label: "Changes" },
  { id: "automation-decisions", label: "Decisions" },
  { id: "automation-retired", label: "Retired" },
] as const;

export function AutomationRecordPage({ name }: { name: string }) {
  const view = useGroupsEdit();
  const perf = useAutomationsPerformance();
  const book = useAutomations();
  if (view.isLoading) return <div className="muted">Reading the automation…</div>;
  if (view.error || !view.data) return <div className="error">Could not read the automations{view.error ? `: ${String(view.error)}` : "."}</div>;
  const found = findAutomation(view.data, name);
  if (found === undefined) {
    return (
      <div className="pf-layout">
        <p className="muted">
          No automation named {name}. <Link to="/automations/groups">Every automation is listed in Groups.</Link>
        </p>
      </div>
    );
  }
  const { m, group } = found;
  const line = perf.data?.arms[name];
  const positions = (book.data?.positions ?? []).filter((p) => p.arm === name);
  const open = positions.filter((p) => p.status === "open").length;
  return (
    <div className="pf-layout">
      <nav className="grp-crumbs" aria-label="Where you are">
        <Link to="/automations/groups">groups</Link>
        {" / "}
        <Link to={`/automations/groups?group=${encodeURIComponent(group ?? "_none")}`}>{group ?? "in no group"}</Link>
        {" / "}
        <b>{name}</b>
      </nav>
      <nav className="ar-jump" aria-label="On this page">
        {JUMPS.map((j) => (
          <a key={j.id} href={`#${j.id}`}>
            {j.label}
          </a>
        ))}
      </nav>
      <Section id="automation-rule" concept="add" tag="the rule" question="What does it place, and is it switched on?">
        <RuleCard name={name} help={HELP} />
        <Card title="where it stands" collapseKey="automations-automation-state" className="lv-4" help={HELP}>
          <dl className="ar-facts">
            <dt>research</dt>
            <dd>{m.verdict === null ? <span className="muted">no register row</span> : <span className={VERDICT_CHIP[m.verdict] ?? "chip"}>{VERDICT_WORDS[m.verdict] ?? m.verdict}</span>}</dd>
            <dt>switches</dt>
            <dd>
              <span className={STATE_CHIP[m.state] ?? "chip"}>{m.state === "waiting" && m.from ? `from ${m.from}` : (STATE_WORDS[m.state] ?? m.state)}</span>
            </dd>
            <dt>group</dt>
            <dd>{group === null ? "none: it answers to its own switch alone" : group}</dd>
          </dl>
          <Link className="btn" to={switchesHref(name, group)}>
            Change in Groups ›
          </Link>
        </Card>
        <Card title="the shape and the chain" collapseKey="automations-automation-shape" className="lv-12" help={HELP}>
          <TradeShape name={name} />
        </Card>
      </Section>
      <WorkingSection name={name} />
      <Section id="automation-record" concept="hist" tag="what it did" question="What has it done on paper?">
        <FliesTile title="net P&L" value={line === undefined ? (perf.data === undefined ? null : "—") : fmtMoney(line.net)} tone={line === undefined ? "dim" : line.net > 0 ? "pos" : line.net < 0 ? "neg" : "dim"} foot="net at the suite's slippage model, closed structures" />
        <FliesTile title="drawdown" value={line === undefined || line.trades === 0 ? "—" : fmtMoney(-line.drawdown)} tone={line !== undefined && line.drawdown > 0 ? "neg" : "dim"} foot="largest peak-to-trough fall of the running net" />
        <FliesTile title="closed trades" value={String(line?.trades ?? 0)} tone="dim" foot={line === undefined || line.trades === 0 ? "none closed yet" : `${String(line.wins)} with a net above zero`} />
        <FliesTile title="open now" value={String(open)} tone="dim" foot="held to the posted close or to its exit" />
        <OpenTable positions={positions} loading={book.isLoading} />
        <ClosedStructures arm={name} />
        <p className="muted lv-12">
          <Link to={`/automations/analysis?an-arm=${encodeURIComponent(name)}`}>
            Its streaks, drawdown episodes, capital and stress test in Analysis ›
          </Link>
        </p>
      </Section>
      <EntriesSection name={name} />
      <SessionsAheadSection module="automations" arm={name} id="automation-ahead" />
      <ChangesSection module="automations" arm={name} id="automation-changes" />
      <Section id="automation-decisions" concept="add" tag="decisions" question="What did it decide this session, and why?">
        <DecisionsCard module="automations" arm={name} />
      </Section>
      <RetiredSection m={m} open={open} />
    </div>
  );
}
