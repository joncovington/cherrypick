import { useState } from "react";
import type { LiveArmChoice, LiveArmLoop, LiveArmSetting } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn } from "../../components/CardHelp";
import { GuideLink } from "../../components/GuideLink";
import { StateLine } from "../Live/liveParts";
import { ConfigError, useLiveArm, useSetLiveArm } from "./useConfigModel";

/**
 * Config > live: which arm each live loop runs.
 *
 * An arm is one configured variant of a module. The loop runs the arm chosen here whenever it is armed for
 * the day; arming itself is on the Live page. The console decides nothing about the change: the
 * orchestrator's own `set_live_arm` refuses it unless the arm has a paper twin and the loop is flat and
 * settled, journals it, and changes that one key. This page offers the choice, says why it is locked when
 * it is, and asks for the typed phrase. A loop it cannot change (another login, the ladder) is shown with
 * the arm it runs and the reason.
 */

export const CONFIRMATION = "CHANGE ARM";

const HELP = {
  title: "Which arm does each live loop run?",
  body: (
    <>
      <HelpSection title="What an arm is">
        <p>
          An arm is one configured variant of a module. A live loop runs one of them. The choice takes effect at the next arming, and arming
          itself stays on the Live page: choosing here arms nothing.
        </p>
      </HelpSection>
      <HelpSection title="Why it can be locked">
        <p>
          The arm can change only when the loop is flat and settled (not armed today, no position, no working order, nothing unsettled), and only to an
          arm that has a paper twin, because live is read per arm and an arm with no twin is measured against nothing. The orchestrator decides, and
          says why in words.
        </p>
      </HelpSection>
      <HelpWarn title="Kill criteria are per arm">
        <p>The live kill criteria are derived and written for one arm and symbol. After a change, check the loop&apos;s status for the new arm before arming.</p>
      </HelpWarn>
      <GuideLink to="/help/going-live#the-locks-in-the-order-they-stand" />
    </>
  ),
};

function show(v: LiveArmSetting | undefined): string {
  return v === undefined ? "unset" : v === null ? "null" : String(v);
}

/** What changes between two arms: each plain setting whose value differs, `key: from → to`. */
export function settingChanges(from: LiveArmChoice | undefined, to: LiveArmChoice): Array<{ key: string; from: string; to: string }> {
  const a = from?.settings ?? {};
  const keys = [...new Set([...Object.keys(a), ...Object.keys(to.settings)])].sort();
  return keys.filter((k) => a[k] !== to.settings[k]).map((k) => ({ key: k, from: show(a[k]), to: show(to.settings[k]) }));
}

export function LoopRow({ loop, initialPicked = null }: { loop: LiveArmLoop; initialPicked?: string | null }) {
  const set = useSetLiveArm();
  const [picked, setPicked] = useState<string | null>(initialPicked);
  const [typed, setTyped] = useState("");
  const choice = picked === null ? undefined : loop.arms.find((a) => a.arm === picked);
  // A change is only ever staged on a loop the orchestrator will change right now: a locked loop, or one
  // the page cannot change at all, never shows the confirmation, whatever state a picker was left in.
  const staged = loop.editable && loop.canChange && choice !== undefined && choice.arm !== loop.current;
  const current = loop.arms.find((a) => a.arm === loop.current);
  const locked = !loop.canChange;
  const done = set.data !== undefined && !staged ? set.data : undefined;

  const reset = () => {
    setPicked(null);
    setTyped("");
    set.reset();
  };
  const save = () => {
    if (choice === undefined) return;
    set.mutate({ arm: choice.arm, confirm: CONFIRMATION }, { onSuccess: () => { setPicked(null); setTyped(""); } });
  };
  const fail = set.error instanceof ConfigError ? set.error : null;
  const inputId = `arm-${loop.loop.replace(/[^a-z0-9]/gi, "-")}`;

  return (
    <div className="la-row">
      <div className="la-name">
        <strong>{loop.label}</strong>
        <span className="muted la-sub">{loop.current === null ? "no arm set: it trades the top level" : `runs ${loop.current}`}</span>
      </div>

      <div className="la-pick">
        {loop.editable ? (
          <>
            <label htmlFor={inputId} className="fine-label">
              arm
            </label>
            <select
              id={inputId}
              className="text-input la-select"
              value={picked ?? loop.current ?? ""}
              disabled={locked || set.isPending}
              onChange={(e) => {
                setPicked(e.target.value);
                setTyped("");
                set.reset();
              }}
            >
              {loop.current !== null && !loop.arms.some((a) => a.arm === loop.current) && <option value={loop.current}>{loop.current} (current, no paper twin)</option>}
              {loop.arms.map((a) => (
                <option key={a.arm} value={a.arm}>
                  {a.arm}
                  {a.arm === loop.current ? " (current)" : ""}
                </option>
              ))}
            </select>
          </>
        ) : null}
        {loop.note !== null && <span className="muted la-note">{loop.note}</span>}
      </div>

      <div className="la-state">
        {loop.editable && loop.blockers.length > 0 && (
          <>
            <span className="chip chip-warn">locked right now</span>
            <ul className="la-blockers">
              {loop.blockers.map((b) => (
                <li key={b} className="muted">
                  {b}
                </li>
              ))}
            </ul>
          </>
        )}
        {loop.editable && loop.blockers.length === 0 && loop.canChange && <span className="chip">flat and settled</span>}
        {done !== undefined && (
          <span className="chip chip-ok" role="status">
            {done.changed ? `now ${done.arm}` : "already the live arm"}
          </span>
        )}
      </div>

      {staged && choice !== undefined && (
        <div className="la-confirm">
          <strong>
            Change {loop.label} from {loop.current ?? "the top level"} to {choice.arm}?
          </strong>
          <p className="muted">
            It takes effect at the next arming and arms nothing. Live results are read per arm, and {choice.arm} is measured against its paper twin{" "}
            <span className="mono">{choice.twin}</span>. The kill criteria are per arm: check the loop&apos;s status for {choice.arm} before arming.
          </p>
          {(() => {
            const changes = settingChanges(current, choice);
            return changes.length === 0 ? null : (
              <ul className="la-changes" aria-label="settings that change">
                {changes.map((c) => (
                  <li key={c.key}>
                    <span className="mono">{c.key}</span> <span className="muted">{c.from}</span> → <span>{c.to}</span>
                  </li>
                ))}
              </ul>
            );
          })()}
          <div className="la-confirm-row">
            <label className="fine-label" htmlFor={`${inputId}-confirm`}>
              Type <strong>{CONFIRMATION}</strong> to confirm
            </label>
            <input
              id={`${inputId}-confirm`}
              className="text-input la-phrase"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && typed === CONFIRMATION && !set.isPending) save();
                if (e.key === "Escape") reset();
              }}
              autoComplete="off"
              spellCheck={false}
              placeholder={CONFIRMATION}
            />
            <button type="button" className="btn lock-btn-resume" disabled={typed !== CONFIRMATION || set.isPending} onClick={save}>
              {set.isPending ? "saving…" : "Save the arm"}
            </button>
            <button type="button" className="btn btn-quiet" onClick={reset}>
              Discard
            </button>
          </div>
          {fail !== null && (
            <p className="lock-error" role="alert">
              {fail.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** The live tab's card: one row per live loop. Hidden entirely, with one line, when no live loop is configured. */
export function LiveArmCard({ updatedAt }: { updatedAt?: number }) {
  const q = useLiveArm();
  const d = q.data;
  if (q.isError) return <StateLine tone="bad">The live arms could not be read.</StateLine>;
  if (d === undefined) return <p className="muted">loading the live loops…</p>;
  if (d.loops.length === 0) return <p className="muted">No live loop is configured on this machine.</p>;
  return (
    <Card title="live arms" className="lv-12" collapseKey="config-live-arm" updatedAt={updatedAt ?? q.dataUpdatedAt} help={HELP} flag="takes effect at the next arming">
      {d.bridge === "unavailable" && <StateLine tone="warn">The orchestrator could not be reached, so nothing here can be changed: {d.error}</StateLine>}
      <div className="la-rows">
        {d.loops.map((l) => (
          <LoopRow key={l.loop} loop={l} />
        ))}
      </div>
    </Card>
  );
}
