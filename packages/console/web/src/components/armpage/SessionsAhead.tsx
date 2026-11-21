import type { AheadModule, AheadRecord, SessionsAhead } from "@console/shared";
import { Card } from "../DataTable";
import { HelpSection, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { useSessionsAhead } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * The sessions ahead for one arm (`core.ahead`, through the module's own command): the next sessions
 * with what each of its dated gates does there, the gates that read a value at the time, and its record
 * on each kind of day once it has enough of them. Every word and figure is Python's; this lays it out.
 */

const HELP: CardHelpContent = {
  title: "The sessions ahead",
  body: (
    <>
      <HelpSection title="What is listed">
        <p>
          The next ten sessions from the market calendar, each with the gates that are set by the date: a Fed decision, an expiry, a half day, an
          event the arm skips or closes ahead of, a weekday it does not trade. A market holiday between two sessions is named. Where the arm&apos;s
          calendar holds no dates for a label, the day says so: it is not read as a quiet day.
        </p>
      </HelpSection>
      <HelpSection title="On every session">
        <p>
          A gate that reads a value, such as a VIX level, cannot be answered ahead: it depends on the value at the time. Those are listed once, with
          what each reads.
        </p>
      </HelpSection>
      <HelpSection title="Its record on these days">
        <p>
          The arm&apos;s own sessions in the module&apos;s current measurement era that fell on each kind of day, against the rest. The net and the
          means show only once both sides have ten sessions; before that the count is shown and nothing is read from it. A gate that refuses every
          entry on its day has no record of its own.
        </p>
      </HelpSection>
    </>
  ),
};

function short(iso: string): string {
  return iso.slice(5);
}

function SessionsCard({ doc }: { doc: SessionsAhead }) {
  return (
    <Card title="the next sessions" collapseKey="ahead-sessions" className="lv-8" help={HELP}>
      <table className="data-table ahead-table">
        <thead>
          <tr>
            <th>session</th>
            <th>close</th>
            <th>what its gates do</th>
          </tr>
        </thead>
        <tbody>
          {doc.sessions.map((s) => (
            <tr key={s.session} className={s.gates.length > 0 ? "ahead-hit" : undefined}>
              <td>
                {s.weekday.slice(0, 3)} {short(s.session)}
                {s.closed_before.length > 0 && <div className="muted ahead-closed">market closed {s.closed_before.map(short).join(", ")}</div>}
              </td>
              <td>{s.early_close ? <span className="chip chip-warn">{s.close}</span> : s.close}</td>
              <td>
                {s.gates.length === 0 && s.unknown.length === 0 && <span className="muted">no dated gate</span>}
                {s.gates.map((g) => (
                  <div key={g.label}>
                    <b>{g.name ?? g.label}</b> {g.effect}
                    {g.depends_on !== null && <span className="muted"> Depends on {g.depends_on}.</span>}
                  </div>
                ))}
                {s.unknown.length > 0 && <div className="muted">The calendar does not say for {s.unknown.join(", ")}.</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ValueGatesCard({ doc }: { doc: SessionsAhead }) {
  return (
    <Card title="on every session" collapseKey="ahead-values" className="lv-4" help={HELP}>
      {doc.value_gates.length === 0 ? (
        <p className="muted">No gate that reads a value at the time is set where it can bind.</p>
      ) : (
        <ul className="ahead-values">
          {doc.value_gates.map((v, i) => (
            <li key={`${v.name ?? ""}-${String(i)}`}>
              <b>{v.name}</b> {v.effect}
              {v.depends_on !== null && <div className="muted">Depends on {v.depends_on}.</div>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function recordWords(r: AheadRecord, min: number): string {
  if (r.readable && r.mean !== null && r.other_mean !== null) {
    return `${fmtMoney(r.mean)} a session over ${String(r.sessions)}, against ${fmtMoney(r.other_mean)} on the other ${String(r.other_sessions)}`;
  }
  if (r.to_go > 0) return `${String(r.sessions)} so far, too few to read: ${String(r.to_go)} more needed`;
  return `${String(r.sessions)} so far, but ${String(r.other_sessions)} other sessions, too few to read against (${String(min)} needed)`;
}

function RecordCard({ doc }: { doc: SessionsAhead }) {
  const records = doc.records ?? [];
  const min = doc.min_sessions ?? 10;
  return (
    <Card title="its record on these days" collapseKey="ahead-record" className="lv-12" help={HELP}>
      <ul className="ahead-values">
        {records.map((r) => (
          <li key={r.label}>
            <b>{r.name ?? r.label}</b> {recordWords(r, min)}
            {r.unknown_sessions > 0 && <span className="muted"> ({String(r.unknown_sessions)} sessions the calendar does not say for)</span>}
          </li>
        ))}
      </ul>
      {doc.era_start !== null && <p className="muted">Sessions from {doc.era_start}, the module&apos;s current measurement era.</p>}
    </Card>
  );
}

export function SessionsAheadBody({ doc }: { doc: SessionsAhead }) {
  if (doc.error !== null) {
    return (
      <Card title="the next sessions" collapseKey="ahead-sessions" className="lv-12" help={HELP}>
        <p className="error">{doc.error}</p>
      </Card>
    );
  }
  return (
    <>
      <SessionsCard doc={doc} />
      <ValueGatesCard doc={doc} />
      {doc.records !== null && doc.records.length > 0 && <RecordCard doc={doc} />}
    </>
  );
}

export function SessionsAheadSection({ module, arm, id }: { module: AheadModule; arm: string; id?: string }) {
  const q = useSessionsAhead(module, arm);
  return (
    <Section id={id} concept="room" tag="the sessions ahead" question="What is coming that will touch its gates?">
      {q.data === undefined ? (
        <Card title="the next sessions" collapseKey="ahead-sessions" className="lv-12" help={HELP}>
          <p className={q.isError ? "error" : "muted"}>{q.isError ? "Could not read the sessions ahead." : "Reading the calendar…"}</p>
        </Card>
      ) : (
        <SessionsAheadBody doc={q.data} />
      )}
    </Section>
  );
}
