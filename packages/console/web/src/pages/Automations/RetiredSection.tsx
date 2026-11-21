import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GroupsEditMember } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { automationRetire } from "../../lib/api";
import { Toggle } from "./Toggle";

/**
 * The foot of an automation's page: whether it is retired, and the switch that retires or reinstates it.
 *
 * Retiring switches it off and locks its switch on the Groups tab; reinstating unlocks it and leaves it off,
 * so it trades again only once switched on in its group, from the next session. Either takes a second click
 * on the strip that says what it does, then goes through the orchestrator's config editor after the
 * automations module's own check (`POST /api/automations/retire`). The reason shown is the module's
 * verdict in plain words ("backtest failed"); the page never names a research item. Paper only.
 */

const HELP: CardHelpContent = {
  title: "Retired",
  body: (
    <>
      <HelpSection title="What retiring does">
        <p>It switches the automation off and keeps it off: its switch on the Groups tab is locked. It enters nothing new. Anything it holds is still managed to its exits, and its record and this page stay.</p>
      </HelpSection>
      <HelpSection title="Reinstating">
        <p>Reinstating unlocks it. It stays off until you switch it on in its group, and then it starts at the next session.</p>
      </HelpSection>
    </>
  ),
};

/** The retired line in words, from the module's state and verdict. */
export function retiredWords(m: Pick<GroupsEditMember, "retired" | "verdict">): string {
  if (m.retired) return `Retired ${m.retired.slice(5)}: ${m.verdict === "failed" ? "backtest failed" : "switched off for good"}.`;
  if (m.verdict === "failed") return "Not retired. Its backtest failed, so retiring it is the usual next step.";
  return "Not retired.";
}

export function RetiredSection({ m, open }: { m: GroupsEditMember; open: number }) {
  const qc = useQueryClient();
  const retired = Boolean(m.retired);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    const r = await automationRetire(m.name, !retired);
    setBusy(false);
    if (!r.ok) {
      setError(r.problems?.length ? r.problems.join("; ") : (r.error ?? "the save failed"));
      return;
    }
    setAsking(false);
    void qc.invalidateQueries({ queryKey: ["automations-groups-edit"] });
    void qc.invalidateQueries({ queryKey: ["automations-groups"] });
    void qc.invalidateQueries({ queryKey: ["automations-builder"] });
  };
  return (
    <Section id="automation-retired" concept="stand" tag="retired" question="Is it retired, and should it be?">
      <Card title="retired" collapseKey="automations-automation-retired" className="lv-12" help={HELP}>
        <div className="ret-head">
          <Toggle on={asking ? !retired : retired} label={`${m.name}: retired`} tone="off" pending={asking} disabled={busy} onChange={() => setAsking(!asking)} />
          <span className={retired ? "ret-state is-retired" : "ret-state"}>{retiredWords(m)}</span>
          {retired && <span className="muted">It is off and stays off until you reinstate it.</span>}
        </div>
        {asking && (
          <div className="ret-confirm" role="group" aria-label={retired ? "Reinstate" : "Retire"}>
            <span>
              <b>
                {retired ? "Reinstate" : "Retire"} {m.name}?
              </b>{" "}
              {retired
                ? "Its switch unlocks but stays off. Switch it on in its group when you want it to trade."
                : `It is switched off and its switch locks. ${open === 0 ? "It holds nothing." : `Its ${open} open position${open === 1 ? " is" : "s are"} managed to ${open === 1 ? "its exit" : "their exits"}.`}`}
            </span>
            <button type="button" className="btn" disabled={busy} onClick={() => setAsking(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void go()}>
              {retired ? "Reinstate" : "Retire"}
            </button>
          </div>
        )}
        {error !== null && (
          <p className="error" role="alert">
            Not saved: {error}
          </p>
        )}
        <dl className="ret-facts">
          <div>
            <dt>Backtest</dt>
            <dd>
              {m.verdict === "failed" ? "Failed" : m.verdict === "passed" ? "Passed" : "No verdict yet"} · <a href="#automation-working">how it compares ›</a>
            </dd>
          </div>
          <div>
            <dt>What retiring does</dt>
            <dd>Switches it off and locks its switch on the Groups tab. It enters nothing new.</dd>
          </div>
          <div>
            <dt>What it keeps</dt>
            <dd>Any open position is managed to its exit. Its record and this page stay.</dd>
          </div>
          <div>
            <dt>Reinstating</dt>
            <dd>Unlocks it. It stays off until you switch it on in its group, from the next session.</dd>
          </div>
        </dl>
      </Card>
    </Section>
  );
}
