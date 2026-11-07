import type { FliesGateSessions, GateArm, GateBlock } from "@console/shared";
import { Card, PnlCell, SortTh, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { fmtMoney } from "../../lib/format";
import { useBridged } from "../../lib/bridged";
import { useOpenParam } from "../Backtest/CardLightbox";
import { gateDescription } from "./gateGlossary";
import { FliesLayout } from "./FliesFilter";
import { Section } from "../../components/Section";
import { FliesBoxHost, type FliesBox } from "./FliesBoxes";

/**
 * Sessions where a gate arm's one variable acted, against control (`run.py gate-sessions`,
 * `analytics.gate_sessions`, bridged). Replaced the arm-divergence card on 2026-10-12: that card
 * answered GEX-centre agreement, a question retired with the `gex` arm on 2026-08-21, and flagged
 * pairs over a console-side 80% threshold.
 *
 * Each arm is read over its OWN window -- the module's `core.regimecuts.era_bounds` over the
 * ledger's `measurement_breaks` (the latest book-wide break, or the arm's own `arm_added` break
 * where later) -- never the console's `ERAS` table. "Acted" is the module's: a refusal of the arm's
 * own gate, a tagged close, or a restacked entry. Counts and sums only; no verdict is drawn here.
 *
 * Laid out 2026-11-07 like the portfolio tab: the summary is the card; an arm's sessions open from its
 * row as a lightbox (`?gate=<arm>`), and the method sits behind the card's ?.
 */

function BlockCells({ b }: { b: GateBlock }) {
  return (
    <>
      <td className="muted">{b.sessions === b.scored ? String(b.sessions) : `${String(b.scored)} of ${String(b.sessions)}`}</td>
      <td><PnlCell v={b.arm_net} /></td>
      <td><PnlCell v={b.control_net} /></td>
      <td><PnlCell v={b.diff} /></td>
      <td className="muted">{b.scored === 0 ? "—" : `${String(b.arm_ahead)} / ${String(b.arm_behind)}`}</td>
    </>
  );
}

function actedLabel(row: GateArm["sessions"][number]): string {
  const parts: string[] = [];
  for (const [reason, n] of Object.entries(row.refusals)) parts.push(`${reason} ×${String(n)}`);
  if (row.close_tags > 0) parts.push(`${String(row.close_tags)} close tag${row.close_tags === 1 ? "" : "s"}`);
  if (row.restacks > 0) parts.push(`${String(row.restacks)} restack${row.restacks === 1 ? "" : "s"}`);
  return parts.length === 0 ? "—" : parts.join(" · ");
}

/** One gate arm's sessions: its own window, what it acts by, then a row a session. The lightbox body. */
export function ArmSessions({ a }: { a: GateArm }) {
  return (
    <>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        window from <strong>{a.window_start ?? "the first row"}</strong>
        {a.window_break !== null && (
          <> ({a.window_break.kind}{a.window_break.scope !== "*" ? `, ${a.window_break.scope}` : ""})</>
        )}
        {" · acts by "}
        {a.acts_by.map((r, i) => (
          <span key={r} title={gateDescription(r) ?? undefined}>
            {i > 0 && ", "}
            <code>{r}</code>
          </span>
        ))}
        {" · refusals are counted per evaluated tick"}
      </p>
      {a.sessions.length === 0 ? (
        <p className="muted">no sessions in this arm&apos;s window yet</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table num-from-3">
            <thead>
              <tr>
                <th>session</th>
                <th>acted</th>
                <th title="entries: arm / control">entries</th>
                <th title="the arm's settled net">arm net</th>
                <th title="control's settled net, same session">control net</th>
                <th title="arm net − control net">diff</th>
              </tr>
            </thead>
            <tbody>
              {a.sessions.map((s) => (
                <tr key={s.session}>
                  <td>
                    {s.session}
                    {s.partial_session && (
                      <span className="chip chip-warn" style={{ marginLeft: 6, fontSize: 9 }} title="a partial_session break: the loop lost part of this session">
                        partial
                      </span>
                    )}
                  </td>
                  <td className={s.acted ? "" : "muted"}>{actedLabel(s)}</td>
                  <td className="muted">{`${String(s.arm_entries)} / ${String(s.control_entries)}`}</td>
                  <td>{s.arm_net === null ? <span className="muted">open</span> : <PnlCell v={s.arm_net} />}</td>
                  <td>{s.control_net === null ? <span className="muted">open</span> : <PnlCell v={s.control_net} />}</td>
                  <td><PnlCell v={s.diff} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** The summary's sorts. `acted` ranks by every session the gate acted on, scored or still open;
 *  ahead / behind is a pair of counts and stays unsorted. */
const GATE_SORTS = {
  arm: (a: GateArm) => a.arm,
  window: (a: GateArm) => a.window_start,
  acted: (a: GateArm) => a.acted.sessions,
  armNet: (a: GateArm) => a.acted.arm_net,
  controlNet: (a: GateArm) => a.acted.control_net,
  diff: (a: GateArm) => a.acted.diff,
  quiet: (a: GateArm) => a.quiet.sessions,
  quietDiff: (a: GateArm) => a.quiet.diff,
};

/** The card's ?: the quiet-session rule leads, as a warning; then what the nets are and how each arm's window is cut. */
export function gatesHelp(d: FliesGateSessions): CardHelpContent {
  return {
    title: "Reading gates vs control",
    body: (
      <>
        <HelpWarn title="Quiet sessions">
          <p>
            Each gate arm is control plus one variable. A session where the arm&apos;s variable never acted should differ from control only
            through cadence and the sign rule, so it is counted apart; one where it acted is where the variable shows.
          </p>
        </HelpWarn>
        <HelpSection title="Nets">
          <p>Settled net after fees. A session still open has none, so it is counted as acted but not scored.</p>
        </HelpSection>
        <HelpSection title="Windows">
          <p>
            Each arm is read over its own window: from the latest break that covers it, or its own arm-added break where later. The era runs from{" "}
            <strong>{d.era_start ?? "the first row"}</strong>
            {d.era_break != null && <> ({d.era_break.kind})</>}.
          </p>
        </HelpSection>
        <HelpSection title="Acted">
          <p>A refusal of the arm&apos;s own gate, a tagged close, or a restacked entry. Counts and sums only; no verdict is drawn here.</p>
        </HelpSection>
      </>
    ),
  };
}

export function GatesBody({ d, updatedAt }: { d: FliesGateSessions; updatedAt?: number }) {
  const sort = useSort();
  const lb = useOpenParam("gate");
  const boxes: FliesBox[] = d.arms.map((a) => ({
    id: a.arm,
    kicker: "gates vs control ›",
    title: `${a.arm} vs ${a.base}`,
    tabs: [{ id: "sessions", label: `${String(a.sessions.length)} session${a.sessions.length === 1 ? "" : "s"}` }],
    body: () => <ArmSessions a={a} />,
  }));
  return (
    <FliesLayout>
      <Section concept="edge" tag="gates" question="Does each gate beat control?">
        <Card title="gates vs control" collapseKey="flies-gates" updatedAt={updatedAt} help={gatesHelp(d)} flag="quiet sessions counted apart">
          <p className="muted sm">
            as of <strong>{d.as_of ?? "—"}</strong>
          </p>
          <div className="table-scroll">
            <table className="data-table num-from-2">
              <thead>
                <tr>
                  <SortTh label="arm" k="arm" sort={sort} />
                  <SortTh label="window" k="window" sort={sort} />
                  <SortTh label="acted" k="acted" sort={sort} title="sessions where the gate acted (scored of total when some are still open)" />
                  <SortTh label="arm net" k="armNet" sort={sort} />
                  <SortTh label="control net" k="controlNet" sort={sort} />
                  <SortTh label="diff" k="diff" sort={sort} />
                  <th title="sessions the arm finished ahead / behind control">ahead / behind</th>
                  <SortTh label="quiet" k="quiet" sort={sort} title="sessions where it never acted" />
                  <SortTh label="quiet diff" k="quietDiff" sort={sort} />
                </tr>
              </thead>
              <tbody>
                {sortRows(d.arms, sort, GATE_SORTS).map((a) => (
                  <tr key={a.arm}>
                    <td>
                      <button type="button" className="pf-more" onClick={() => lb.show(a.arm)} aria-label={`Open ${a.arm}'s sessions`}>
                        {a.arm} <span aria-hidden="true">›</span>
                      </button>
                    </td>
                    <td className="muted">{a.window_start ?? "—"}</td>
                    <BlockCells b={a.acted} />
                    <td className="muted">{String(a.quiet.sessions)}</td>
                    <td>{fmtMoney(a.quiet.diff)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Section>
      <FliesBoxHost lb={lb} boxes={boxes} />
    </FliesLayout>
  );
}

export function GatesSlide() {
  const { data, isLoading, dataUpdatedAt } = useBridged<FliesGateSessions>(["flies-gates"], "/api/flies/gates");
  const d = data?.data ?? null;

  if (isLoading) {
    return (
      <FliesLayout>
        <Section concept="edge" tag="gates" question="Does each gate beat control?">
          <Card title="gates vs control">
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          </Card>
        </Section>
      </FliesLayout>
    );
  }
  if (data === undefined || !data.ok || d === null) {
    return (
      <FliesLayout>
        <Section concept="edge" tag="gates" question="Does each gate beat control?">
          <Card title="gates vs control" isError>
            <p className="muted">{data?.error ?? "the gate-sessions read is unavailable"}</p>
          </Card>
        </Section>
      </FliesLayout>
    );
  }
  if (d.ledger === "absent") {
    return (
      <FliesLayout>
        <Section concept="edge" tag="gates" question="Does each gate beat control?">
          <Card title="gates vs control">
            <p className="muted">No flies paper ledger on this machine.</p>
          </Card>
        </Section>
      </FliesLayout>
    );
  }
  return <GatesBody d={d} updatedAt={dataUpdatedAt} />;
}
