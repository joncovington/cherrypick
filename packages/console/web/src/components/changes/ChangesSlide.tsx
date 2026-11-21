import type { ChangeReading, ChangeRow, ChangesModule, ModuleChanges } from "@console/shared";
import { Card, DataCard } from "../DataTable";
import { HelpSection, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { FliesTile } from "../../pages/Flies/FliesSession";
import { useModuleChanges } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * Where a change is decided: every modification under test in one module (`core.changes`, bridged by
 * `/api/changes/:module`). Advisor experiments against their base, arms against their control, and
 * for automations the ideas and settings declared from the Builder. Each row's reading and its
 * sessions to a decision are core's; the page lays them out and words them.
 */

const HELP: CardHelpContent = {
  title: "Changes",
  body: (
    <>
      <HelpSection title="What is listed">
        <p>
          Every variant being measured against what it would replace: an advisor experiment's twin against its base, a configured arm against its
          control, and an idea or setting declared from the Builder, which gets one test on sessions its search never read.
        </p>
      </HelpSection>
      <HelpSection title="The gap">
        <p>
          The variant's net less the other's, paired session by session, per trade where the module records entries (so a variant that trades more
          is not read as better for its size), with its 95% interval.
        </p>
      </HelpSection>
      <HelpSection title="The reading">
        <p>
          <b>Ready</b>: the interval is clear of zero. <b>Leaning</b>: at its size, the gap would be clear within as many sessions again.{" "}
          <b>Too early</b>: fewer than ten paired sessions, or no decision in reach. <b>Waiting</b>: a declared idea or setting before its one
          held-out test. <b>Closed</b>: an experiment that expired or was stopped, or a test that has run. A reading is a forward read of paper,
          not a verdict on the rule.
        </p>
      </HelpSection>
    </>
  ),
};

export const READING_WORDS: Record<ChangeReading, string> = {
  ready: "ready",
  leaning: "leaning",
  too_early: "too early",
  waiting: "waiting on its test",
  closed: "closed",
};
const READING_CHIP: Record<ChangeReading, string> = {
  ready: "chip chip-ok",
  leaning: "chip chip-info",
  too_early: "chip",
  waiting: "chip chip-warn",
  closed: "chip",
};

/** How a row is tested, in words: what kind of change, and what it is measured against. */
export function howWords(r: ChangeRow): { kind: string; against: string } {
  if (r.kind === "advised") return { kind: "advisor experiment", against: `vs ${r.against}` };
  if (r.kind === "arm") return { kind: "arm", against: `vs ${r.against}` };
  return { kind: `declared ${r.source?.kind ?? "item"}`, against: "one held-out test" };
}

/** The gap a row is read on, with its interval. */
export function gapWords(r: ChangeRow): string {
  const g = r.gap;
  if (g === null) return "—";
  const perTrade = r.basis === "per_trade" && g.per_trade !== null;
  const v = perTrade ? g.per_trade : g.per_session;
  const ci = perTrade ? g.per_trade_ci : g.per_session_ci;
  if (v === null) return "—";
  const unit = perTrade ? "a trade" : "a session";
  return ci ? `${fmtMoney(v)} ${unit} [${fmtMoney(ci[0])}, ${fmtMoney(ci[1])}]` : `${fmtMoney(v)} ${unit}`;
}

/** The reading in words, with its direction where it has one. */
export function readingWords(r: ChangeRow): string {
  const base = READING_WORDS[r.reading];
  if (r.kind === "declared" && r.reading === "closed") return `tested: ${(r.held_out?.verdict ?? "no verdict").replace(/_/g, " ")}`;
  if (r.direction === null || r.direction === "even" || r.reading === "too_early") return base;
  return `${base} ${r.direction}`;
}

function toDecision(r: ChangeRow): string {
  if (r.reading === "closed" || r.reading === "ready") return "—";
  if (r.reading === "waiting") return "its test";
  return r.sessions_to_decision === null ? "out of reach" : `${String(r.sessions_to_decision)} more`;
}

function ChangesTable({ title, rows }: { title: string; rows: ChangeRow[] }) {
  return (
    <DataCard
      title={title}
      className="lv-12"
      headers={["change", "tested", "gap", "sessions", "reading", "to a decision"]}
      rowCount={rows.length}
      loading={false}
      numFrom={3}
      empty="nothing here"
      help={HELP}
    >
      {rows.map((r) => (
        <tr key={`${r.kind}|${r.arm}|${r.id ?? ""}`}>
          <td>
            <div className="chg-name">{r.name}</div>
            {r.description && <div className="muted chg-desc">{r.description}</div>}
            {r.id && r.id !== r.name && <div className="muted chg-id">{r.id}</div>}
          </td>
          <td className="chg-how">
            <div>{howWords(r).kind}</div>
            <div className="muted chg-desc">{howWords(r).against}</div>
          </td>
          <td>{gapWords(r)}</td>
          <td>{r.sessions ?? "—"}</td>
          <td>
            <span className={READING_CHIP[r.reading]}>{readingWords(r)}</span>
          </td>
          <td>{toDecision(r)}</td>
        </tr>
      ))}
    </DataCard>
  );
}

export function ChangesBody({ data, arm = null }: { data: ModuleChanges; arm?: string | null }) {
  if (data.error !== null) {
    return (
      <Card title="changes" isError help={HELP}>
        <p className="muted">{data.error}</p>
      </Card>
    );
  }
  const rows = arm === null ? data.rows : data.rows.filter((r) => r.arm === arm);
  const open = rows.filter((r) => r.reading !== "closed");
  const closed = rows.filter((r) => r.reading === "closed");
  const count = (k: ChangeReading) => open.filter((r) => r.reading === k).length;
  return (
    <>
      {arm === null && (
        <>
          <FliesTile title="ready" value={String(count("ready"))} tone={count("ready") ? "pos" : "dim"} foot="the interval is clear of zero" help={HELP} />
          <FliesTile title="leaning" value={String(count("leaning"))} tone="dim" foot="clear within as many sessions again, at its size" />
          <FliesTile title="too early" value={String(count("too_early"))} tone="dim" foot={`under ${String(data.min_sessions ?? 10)} sessions, or out of reach`} />
          <FliesTile title="waiting" value={String(count("waiting"))} tone="dim" foot="declared, before its one held-out test" />
        </>
      )}
      {rows.length === 0 ? (
        <Card title="changes" help={HELP}>
          <p className="muted">Nothing is under test{arm === null ? " in this module" : " for it"}.</p>
        </Card>
      ) : (
        <>
          <ChangesTable title="under test" rows={open} />
          {closed.length > 0 && <ChangesTable title="closed" rows={closed} />}
        </>
      )}
      {data.withheld > 0 && (
        <p className="muted lv-12">
          {data.withheld} declared {data.withheld === 1 ? "item is" : "items are"} not shown: {data.withheld === 1 ? "its run is" : "their runs are"}{" "}
          licensed, and licensed results are hidden now.
        </p>
      )}
    </>
  );
}

/** A module's `changes` tab. */
export function ChangesSlide({ module }: { module: ChangesModule }) {
  const q = useModuleChanges(module);
  return (
    <div className="pf-layout">
      <Section concept="edge" tag="changes" question="Which changes are under test, and is any ready to decide?">
        {q.data === undefined ? (
          <Card title="changes" help={HELP}>
            <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read the changes." : "Reading the changes…"}</p>
          </Card>
        ) : (
          <ChangesBody data={q.data} />
        )}
      </Section>
    </div>
  );
}

/** One arm's slice, for its own page. */
export function ChangesSection({ module, arm, id }: { module: ChangesModule; arm: string; id?: string }) {
  const q = useModuleChanges(module);
  return (
    <Section id={id} concept="edge" tag="changes" question="Which changes to it are under test?">
      {q.data === undefined ? (
        <Card title="changes" help={HELP}>
          <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read the changes." : "Reading the changes…"}</p>
        </Card>
      ) : (
        <ChangesBody data={q.data} arm={arm} />
      )}
    </Section>
  );
}
