import { useState } from "react";
import { Link } from "react-router-dom";
import type { ConfigModelPayload, ManageAttentionLeg, ManageIntentCandidate, ManageVerdictBrief } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { intentOf, intentWords, stageIntent } from "../Config/intents";
import { useStagedVersion } from "../Config/stagedStore";
import { useConfigModel } from "../Config/useConfigModel";
import { ACTION_LABEL } from "./manageParts";

/**
 * Live → wheel's "is this a wheel?" (2026-11-12): manage lists each short put on a name with no intent
 * (`wheel.intentCandidates`) with today's verdict and the one it would get marked as a wheel. A row
 * already in the needs-a-look cards gets a chip (the choice matters most there); the rest sit in a
 * card of their own. A choice is only staged: it is saved on Config › Position rules, the console's
 * one write path, and the evaluation reads it next run. Nothing here judges a trade.
 */

const money = (v: number | null) => (v === null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);

/** The candidate an attention row belongs to: the same name, expiry and strike, a short put. */
export function candidateFor(cands: ManageIntentCandidate[], leg: ManageAttentionLeg): ManageIntentCandidate | null {
  if (leg.right !== "P" || leg.quantity >= 0 || leg.strike === null) return null;
  return cands.find((c) => c.underlying === leg.underlying && c.expiration === leg.expiration && c.strikes.includes(leg.strike!)) ?? null;
}

function Verdict({ v }: { v: ManageVerdictBrief }) {
  return (
    <span className="int-verdict" title={v.reason ?? undefined}>
      {v.action === null ? "—" : ACTION_LABEL[v.action] ?? v.action.toLowerCase()}
      {v.rule !== null && <span className="muted"> · {v.rule}</span>}
    </span>
  );
}

/** The two outcomes side by side, with the buttons that stage each. */
function Choice({ c, model, onDone }: { c: ManageIntentCandidate; model: ConfigModelPayload | undefined; onDone?: () => void }) {
  const [why, setWhy] = useState<string | null>(null);
  const pick = (intent: string) => {
    const err = stageIntent(model, c.underlying, intent);
    setWhy(err);
    if (err === null) onDone?.();
  };
  return (
    <div className="int-panel">
      <div className="int-col">
        <span className="lk-sub">as it stands</span>
        <Verdict v={c.now} />
        {/* A full button like its neighbour: the quiet style read as plain text on the page. */}
        <button type="button" className="btn" onClick={() => pick("no_wheel")}>
          Not a wheel
        </button>
      </div>
      <div className="int-col is-wheel">
        <span className="lk-sub">as a wheel</span>
        <Verdict v={c.asWheel} />
        <span className="lk-sub">
          {c.contracts !== null ? `${c.contracts * 100} sh` : "shares"}
          {c.netIfAssigned !== null ? ` at ${c.netIfAssigned.toFixed(2)} net` : ""} · {money(c.cashSecured)} already set aside
        </span>
        <button type="button" className="btn" onClick={() => pick("wheel")}>
          Wheel it
        </button>
      </div>
      {!c.wheelPath && <p className="lk-sub int-note">No reconciled cycle holds this put yet, so marking it changes nothing today.</p>}
      {c.earnings !== null && <p className="lk-sub int-note">Earnings {c.earnings} fall inside the put; a wheel put is held through them.</p>}
      {why !== null && <p className="cfg-save-error int-note">{why}</p>}
    </div>
  );
}

/** What a staged or saved choice reads as, with the way to save it. */
function Chosen({ c, model }: { c: ManageIntentCandidate; model: ConfigModelPayload | undefined }) {
  const { intent, unsaved } = intentOf(model, c.underlying);
  if (intent === null) return null;
  return (
    <span className="int-chosen">
      <span className={intent === "wheel" ? "chip int-chip-wheel" : "chip"}>{intentWords(intent)}</span>
      {unsaved && (
        <>
          <span className="lk-sub">unsaved · </span>
          <Link to="/config/rules" className="lk-sub">
            save in Config
          </Link>
          <button type="button" className="btn btn-quiet int-undo" onClick={() => stageIntent(model, c.underlying, null)}>
            undo
          </button>
        </>
      )}
    </span>
  );
}

/** The chip on a needs-a-look row: "no intent", opening the two outcomes. It reads the config model
 *  itself, so the tab asks for it only when a short put waits on an intent. */
export function IntentChip({ c }: { c: ManageIntentCandidate }) {
  useStagedVersion();
  const model = useConfigModel().data;
  const [open, setOpen] = useState(false);
  if (intentOf(model, c.underlying).intent !== null) return <Chosen c={c} model={model} />;
  return (
    <>
      <button type="button" className="int-chip" aria-expanded={open} onClick={() => setOpen(!open)}>
        no intent {open ? "▾" : "▸"}
      </button>
      {open && <Choice c={c} model={model} onDone={() => setOpen(false)} />}
    </>
  );
}

/** The candidates no needs-a-look row shows. Hidden when there are none. */
export function NoIntentCard({ cands, shown }: { cands: ManageIntentCandidate[]; shown: ReadonlySet<string> }) {
  useStagedVersion();
  const model = useConfigModel().data;
  const rest = cands.filter((c) => !shown.has(candidateKey(c)));
  if (rest.length === 0) return null;
  const waiting = rest.filter((c) => intentOf(model, c.underlying).intent === null).length;
  return (
    <Card
      title="no intent yet"
      className={`wh-half${waiting > 0 ? " int-card-waiting" : ""}`}
      help={{
        title: "No intent yet",
        body: (
          <>
            <HelpSection title="Which trades">
              <p>
                Short puts alone, on a name with no intent, that are not near their strike or expiring. Until you choose, a put keeps its
                stop and its breached-strike close.
              </p>
            </HelpSection>
            <HelpSection title="What a choice does">
              <p>
                Wheel it: a tested put may be assigned instead of stopped. Not a wheel: it keeps its stop and leaves this list. Either is saved on
                Config › Position rules, and the evaluation reads it next run.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      <ul className="lk-list">
        {rest.map((c) => (
          <NoIntentRow key={`${c.underlying}-${c.legs.join("|")}`} c={c} model={model} />
        ))}
      </ul>
    </Card>
  );
}

function NoIntentRow({ c, model }: { c: ManageIntentCandidate; model: ConfigModelPayload | undefined }) {
  const [open, setOpen] = useState(false);
  const chosen = intentOf(model, c.underlying).intent !== null;
  return (
    <li className="lk-li">
      <div className="lk-li-top">
        <span className="lk-leg">
          {c.underlying} {c.legs.join(", ")}
        </span>
        {chosen ? (
          <Chosen c={c} model={model} />
        ) : (
          <button type="button" className="int-chip" aria-expanded={open} onClick={() => setOpen(!open)}>
            is it a wheel? {open ? "▾" : "▸"}
          </button>
        )}
      </div>
      <span className="lk-sub">
        {money(c.cashSecured)} cash secured{c.dte !== null ? ` · ${c.dte} DTE` : ""}
        {c.history.cyclesComplete > 0 ? ` · ${c.history.cyclesComplete} cycle${c.history.cyclesComplete === 1 ? "" : "s"} done` : ""}
      </span>
      {open && !chosen && <Choice c={c} model={model} onDone={() => setOpen(false)} />}
    </li>
  );
}

/** The key NoIntentCard matches a row by. */
export function candidateKey(c: ManageIntentCandidate): string {
  return c.legs.join("|") + c.underlying;
}
