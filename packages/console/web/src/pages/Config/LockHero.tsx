import { useState } from "react";
import type { LockStatusPayload, ModuleGateView } from "@console/shared";
import { useLockStatus, useSetLock } from "./useConfigModel";

/**
 * The live-trading lock.
 *
 * The suite halt flag is the one live control that a click may own: its presence stops new live
 * entries everywhere within a tick, and every live loop polls the same file. So it sits at the top
 * of this page, and the friction is deliberately lopsided — halting is one click, because a stop
 * that takes two steps arrives late, while resuming asks for a typed confirmation, because that is
 * the direction that lets money move.
 *
 * The breakdown underneath exists so the hero can't imply more than it does: clearing the halt arms
 * nothing by itself. Each module still has its own gate, and flies still needs a per-day arm record
 * that only its own confirmation ritual writes.
 */

const CONFIRMATION = "RESUME LIVE";

function GateRow({ gate, arm }: { gate: ModuleGateView; arm: LockStatusPayload["fliesArm"] }) {
  const live = gate.liveEnabled;
  const isFlies = gate.id === "flies";
  return (
    <div className="lock-gate">
      <span className="lock-gate-name">{gate.id}</span>
      {live === null ? (
        <span className="chip chip-missing" title="no readable config — unknown, not off">
          unknown
        </span>
      ) : live ? (
        <span className="chip chip-live">live enabled</span>
      ) : (
        <span className="chip">paper only</span>
      )}
      {isFlies && gate.gate0Confirmed === false && live === true && (
        <span className="chip chip-warn" title="live.gate0_confirmed is empty">
          no attestation
        </span>
      )}
      {isFlies &&
        (arm.armed ? (
          <span className="chip chip-ok">armed for {arm.date}</span>
        ) : arm.stale ? (
          <span className="chip chip-warn" title="a record from a previous day; the loop self-disarms on it">
            stale arm ({arm.date})
          </span>
        ) : (
          <span className="chip">not armed today</span>
        ))}
    </div>
  );
}

export function LockHero() {
  const { data, isError } = useLockStatus();
  const setLock = useSetLock();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");

  const halted = data?.halted ?? false;
  const liveModules = (data?.modules ?? []).filter((m) => m.liveEnabled === true);
  const unknownModules = (data?.modules ?? []).filter((m) => m.liveEnabled === null);

  const resume = () => {
    setLock.mutate(
      { present: false, confirm: CONFIRMATION },
      {
        onSuccess: () => {
          setConfirming(false);
          setTyped("");
        },
      },
    );
  };

  return (
    <section className={`lock-hero ${halted ? "halted" : ""}`} aria-live="polite">
      <div className="lock-hero-main">
        <div className="lock-hero-state">
          <h2 className="lock-hero-title">{halted ? "Live entries halted" : "Live entries permitted"}</h2>
          <p className="lock-hero-sub muted">
            {halted ? (
              <>
                The suite halt flag is set — every live loop stops opening new positions within one tick. Open
                positions still follow their normal exit rules.
              </>
            ) : (
              <>
                No halt flag. Each module's own gate decides whether it trades live
                {liveModules.length > 0 ? (
                  <>
                    {" "}
                    — <strong>{liveModules.map((m) => m.id).join(", ")}</strong>{" "}
                    {liveModules.length === 1 ? "is" : "are"} live-enabled right now.
                  </>
                ) : (
                  <> — none is live-enabled right now.</>
                )}
              </>
            )}
          </p>
          {data !== undefined && <div className="lock-hero-path muted">{data.haltFlagPath}</div>}
        </div>

        <div className="lock-hero-action">
          {!halted && (
            <button
              type="button"
              className="btn lock-btn-halt"
              onClick={() => setLock.mutate({ present: true })}
              disabled={setLock.isPending}
            >
              {setLock.isPending ? "halting…" : "Halt live entries"}
            </button>
          )}
          {halted && !confirming && (
            <button type="button" className="btn lock-btn-resume" onClick={() => setConfirming(true)}>
              Resume live entries…
            </button>
          )}
        </div>
      </div>

      {halted && confirming && (
        <div className="lock-confirm">
          <p>
            Clearing the halt lets any live-enabled module open new positions again. It arms nothing on its own —
            flies still needs today's arm record from <code>/live-flies-start</code>.
          </p>
          <div className="lock-confirm-row">
            <label className="fine-label" htmlFor="lock-confirm-input">
              Type <strong>{CONFIRMATION}</strong> to confirm
            </label>
            <input
              id="lock-confirm-input"
              className="text-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && typed === CONFIRMATION) resume();
                if (e.key === "Escape") setConfirming(false);
              }}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              placeholder={CONFIRMATION}
            />
            <button
              type="button"
              className="btn lock-btn-resume"
              disabled={typed !== CONFIRMATION || setLock.isPending}
              onClick={resume}
            >
              {setLock.isPending ? "clearing…" : "Clear the halt"}
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                setConfirming(false);
                setTyped("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {setLock.isError && <p className="lock-error">{(setLock.error as Error).message}</p>}
      {isError && <p className="lock-error">Could not read the lock status — the values below may be stale.</p>}

      <div className="lock-gates">
        {(data?.modules ?? []).map((m) => (
          <GateRow key={m.id} gate={m} arm={data?.fliesArm ?? { armed: false, date: null, at: null, stale: false }} />
        ))}
        {unknownModules.length > 0 && (
          <p className="fine-label muted">
            {unknownModules.length === 1 ? "One module has" : `${String(unknownModules.length)} modules have`} no
            readable config — shown as unknown rather than assumed off.
          </p>
        )}
      </div>
    </section>
  );
}
