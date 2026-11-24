import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { ArmAttempt, LiveArmReadout, LiveLoopSwitch, LiveTodayLoop } from "@console/shared";
import { etClock } from "../../lib/etTime";
import { LIVE_ARMING_KEY, fetchReadout, postArm, postDisarm } from "../../lib/liveArming";
import { pushToast } from "../../lib/toast";

/**
 * The arm-for-today strip on a live loop's card, and the two dialogs behind its switch.
 *
 * The one control on the Live page that is not a read. It holds no arming logic: the dialog shows the
 * module's own readiness and the disclaimer the server read from DISCLAIMER.md, and what the person
 * types goes to the console's route, which checks it again and asks the module's own command. The
 * switch never moves on a click. It shows what the server's state says, so a refusal leaves it where it was.
 */

export const HALT_LOCK = "Clear the halt first. Arming never undoes a halt.";
const CONFIRM_WORD = "YES";

/** The box holds the word and nothing else: no trimming, no case folding. The server checks it again. */
export const isExactYes = (typed: string): boolean => typed === CONFIRM_WORD;
/** A failed attempt stays on the card this long, so it is read even if the notice was missed. */
const FAILURE_SHOWN_MS = 30 * 60_000;

export interface StripModel {
  state: "armed" | "unarmed" | "pending" | "locked";
  /** Armed, or arming: the switch reads on. */
  on: boolean;
  title: string;
  sub: string;
  lockedReason: string | null;
}

/** What the strip says, from the loop's own reading and the server's attempt. Pure. */
export function stripModel(l: LiveTodayLoop, sw: LiveLoopSwitch, attempt: ArmAttempt | null, halted: boolean): StripModel {
  const armed = l.arm.armed && !l.arm.pastDisarm;
  const where = `${l.arm.symbol ?? "no symbol"} · arm ${l.arm.arm ?? "not set"} · self-disarms ${l.arm.disarmTime} ET`;
  if (attempt !== null && attempt.state === "running") {
    return {
      state: "pending",
      on: true,
      title: "Arming…",
      sub: `Started ${etClock(attempt.startedAt, "?")}. You can leave this page.`,
      lockedReason: null,
    };
  }
  // A second login's account cannot be armed here, whatever it reads; an armed loop can still be switched off.
  if (sw.state === "locked" && !armed) {
    return { state: "locked", on: false, title: "Cannot arm here", sub: sw.lockedReason ?? "", lockedReason: sw.lockedReason };
  }
  if (armed) return { state: "armed", on: true, title: "Armed for today", sub: where, lockedReason: null };
  if (halted) return { state: "locked", on: false, title: "Not armed today", sub: HALT_LOCK, lockedReason: HALT_LOCK };
  return { state: "unarmed", on: false, title: "Not armed today", sub: `${l.arm.symbol ?? "no symbol"} · arm ${l.arm.arm ?? "not set"} · self-disarms ${l.arm.disarmTime} ET`, lockedReason: null };
}

/** The attempt that finished badly, while it is still worth reading. */
export function failedAttempt(a: ArmAttempt | null, now = Date.now()): ArmAttempt | null {
  if (a === null || a.state !== "failed" || a.finishedAt === null) return null;
  return now - Date.parse(a.finishedAt) < FAILURE_SHOWN_MS ? a : null;
}

export function ArmStrip({
  l,
  sw,
  attempt,
  halted,
}: {
  l: LiveTodayLoop;
  sw: LiveLoopSwitch;
  attempt: ArmAttempt | null;
  halted: boolean;
}) {
  const m = stripModel(l, sw, attempt, halted);
  const [dialog, setDialog] = useState<"arm" | "disarm" | null>(null);
  const failed = failedAttempt(attempt);
  const locked = m.state === "locked";
  const onClick = () => {
    if (locked || m.state === "pending") return;
    setDialog(m.state === "armed" ? "disarm" : "arm");
  };
  return (
    <>
      <div className={`arm-strip${m.on ? " is-on" : ""}${locked ? " is-locked" : ""}`} data-arm-state={m.state}>
        <div className="arm-strip-text">
          <b>{m.title}</b>
          <span>{m.sub}</span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={m.on}
          aria-busy={m.state === "pending" ? true : undefined}
          aria-disabled={locked || m.state === "pending" ? true : undefined}
          aria-label={
            locked
              ? `Arm ${l.loop} for today (blocked)`
              : m.state === "pending"
                ? `Arming ${l.loop} for today`
                : m.on
                  ? `Switch ${l.loop} off for today`
                  : `Arm ${l.loop} for today`
          }
          title={m.lockedReason ?? undefined}
          className="arm-switch-btn"
          onClick={onClick}
        >
          <span className={`arm-switch${m.on ? " on" : ""}${locked ? " locked" : ""}`}>
            <i />
          </span>
        </button>
      </div>
      {m.state === "pending" && attempt !== null ? <ArmProgress a={attempt} /> : null}
      {failed !== null ? (
        <div className="arm-failed" role="alert">
          <b>Arming did not finish cleanly</b>
          <span>{failed.message}</span>
          {failed.detail.map((d, i) => (
            <span key={i} className="muted">
              {d}
            </span>
          ))}
        </div>
      ) : null}
      {dialog === "arm" ? <ArmDialog loop={l.loop} onClose={() => setDialog(null)} /> : null}
      {dialog === "disarm" ? <DisarmDialog loop={l.loop} onClose={() => setDialog(null)} /> : null}
    </>
  );
}

function ArmProgress({ a }: { a: ArmAttempt }) {
  const waiting = a.step === "waiting";
  return (
    <div className="arm-progress" aria-live="polite">
      <span>
        <b className="pnl-pos">✓</b> You typed YES and accepted the disclaimer
      </span>
      <span>
        <b className={a.recordWritten ? "pnl-pos" : "arm-amber"}>{a.recordWritten ? "✓" : "…"}</b> {a.recordWritten ? `Arm record written for ${a.session}` : "Writing the arm record"}
      </span>
      {a.needsSupervisor ? (
        <span>
          <b className="arm-amber">…</b> {waiting || !a.recordWritten ? "Waiting for the supervisor (up to a minute)" : "Checking the loop"}
        </span>
      ) : (
        <span>
          <b className="arm-amber">…</b> Checking the loop
        </span>
      )}
      <span className="muted">It runs on the server. An amber dot stays on Live in the rail until it finishes; a notice appears on any page.</span>
    </div>
  );
}

// ------------------------------------------------------------------------------ the dialog shell

function Sheet({ title, kick, sub, onClose, children, foot }: { title: string; kick: string; sub?: string; onClose: () => void; children: React.ReactNode; foot: React.ReactNode }) {
  const labelId = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus({ preventScroll: true });
    };
  }, [onClose]);
  const sheet = (
    <div className="arm-wrap">
      <button type="button" className="arm-scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div ref={box} className="arm-box" role="dialog" aria-modal="true" aria-labelledby={labelId} tabIndex={-1}>
        <div className="arm-head">
          <span className="arm-kick">{kick}</span>
          <h2 id={labelId}>{title}</h2>
          {sub !== undefined ? <span className="muted">{sub}</span> : null}
        </div>
        <div className="arm-body">{children}</div>
        <div className="arm-foot">{foot}</div>
      </div>
    </div>
  );
  // Portalled to the body in the browser; inline where there is no document (a server render, a test).
  return typeof document === "undefined" ? sheet : createPortal(sheet, document.body);
}

function Chip({ tone, children }: { tone?: "ok" | "bad" | "warn"; children: React.ReactNode }) {
  return <span className={`chip${tone ? ` chip-${tone}` : ""}`}>{children}</span>;
}

// ------------------------------------------------------------------------------ the arm dialog

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; readout: LiveArmReadout };

export function ArmDialog({ loop, onClose }: { loop: string; onClose: () => void }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [note, setNote] = useState<string | null>(null);
  const qc = useQueryClient();
  useEffect(() => {
    let live = true;
    fetchReadout(loop).then(
      (readout) => live && setLoad({ kind: "ready", readout }),
      (e: Error) => live && setLoad({ kind: "error", message: e.message }),
    );
    return () => {
      live = false;
    };
  }, [loop]);

  if (load.kind === "loading") {
    return (
      <Sheet kick="Reading the loop" title={`Arm ${loop} for today`} onClose={onClose} foot={<button type="button" className="arm-btn" onClick={onClose}>Close</button>}>
        <p className="muted">Asking the loop's own readiness check. It reads files and the ledger, never the broker.</p>
      </Sheet>
    );
  }
  if (load.kind === "error") {
    return (
      <Sheet kick="Nothing to confirm" title={`${loop} could not be read`} onClose={onClose} foot={<button type="button" className="arm-btn" onClick={onClose}>Close</button>}>
        <p className="pnl-neg">{load.message}</p>
        <p className="muted">Nothing was armed.</p>
      </Sheet>
    );
  }
  const r = load.readout;
  const can = r.ok && r.ready && !r.halted && !r.armedToday && r.dialogId !== null && r.disclaimer !== null;
  if (!can) return <RefusedDialog r={r} onClose={onClose} />;
  return (
    <ConfirmDialog
      key={r.dialogId}
      r={r}
      note={note}
      onClose={onClose}
      onChanged={(fresh, why) => {
        setNote(why);
        setLoad({ kind: "ready", readout: fresh });
      }}
      onStarted={(a) => {
        void qc.invalidateQueries({ queryKey: LIVE_ARMING_KEY });
        pushToast({ tone: "info", title: `Arming ${a.loop}`, message: "It runs on the server. You can leave this page.", ttlMs: 6000 });
        onClose();
      }}
    />
  );
}

export function RefusedDialog({ r, onClose }: { r: LiveArmReadout; onClose: () => void }) {
  const why: string[] = [];
  if (!r.ok) why.push(r.error ?? "the loop's status could not be read");
  if (r.halted) why.push(HALT_LOCK);
  if (r.armedToday) why.push("It is already armed for today.");
  if (r.ok && r.disclaimer === null) why.push("DISCLAIMER.md was not found, so it cannot be shown. Nothing can be armed without it.");
  const blockers = r.blockers;
  return (
    <Sheet
      kick="Nothing to confirm"
      title={`${r.title} cannot arm today`}
      sub="The loop's own readiness check says no. There is no disclaimer to accept and no way around it from here."
      onClose={onClose}
      foot={<button type="button" className="arm-btn" onClick={onClose}>Close</button>}
    >
      <Chip tone="bad">
        <span className="dot" /> not ready{blockers.length > 0 ? ` · ${String(blockers.length)} blocker${blockers.length === 1 ? "" : "s"}` : ""}
      </Chip>
      {blockers.map((b, i) => (
        <div key={i} className="arm-blocker">
          {b}
        </div>
      ))}
      {why.map((w, i) => (
        <div key={`w${String(i)}`} className="arm-blocker">
          {w}
        </div>
      ))}
      <span className="muted arm-fine">Each blocker is the loop's own wording, shown as it came back. The console re-derives none of them.</span>
    </Sheet>
  );
}

export function ConfirmDialog({
  r,
  note,
  onClose,
  onChanged,
  onStarted,
}: {
  r: LiveArmReadout;
  note: string | null;
  onClose: () => void;
  onChanged: (fresh: LiveArmReadout, why: string) => void;
  onStarted: (a: ArmAttempt) => void;
}) {
  // Starts empty every time, and a fresh readout remounts this component (keyed by its token): no
  // confirmation survives a re-read.
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus({ preventScroll: true }), []);
  const exact = isExactYes(typed);
  const go = async () => {
    if (!exact || busy || r.dialogId === null) return;
    setBusy(true);
    setError(null);
    try {
      const reply = await postArm(r.loop, r.dialogId, typed);
      if (reply.kind === "started") return onStarted(reply.attempt);
      if (reply.kind === "changed" && reply.refusal.readout !== undefined) {
        return onChanged(reply.refusal.readout, reply.refusal.error);
      }
      if (reply.refusal.readout !== undefined) {
        // Halted, or no longer ready: the module's answer replaces the confirmation.
        return onChanged(reply.refusal.readout, reply.refusal.error);
      }
      setError(reply.refusal.error);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const live = r.loop === "ladder" ? r.mode === "live" : true;
  return (
    <Sheet
      kick="Step 1 of 2 · read this, then confirm"
      title={`Arm ${r.title} for today`}
      sub={`${live ? "Real money, in the real account." : "Dry-run: the loop reads the broker and places nothing."} Arms ${r.session ?? "today"} only; tomorrow needs a fresh confirmation.`}
      onClose={onClose}
      foot={
        <>
          <button type="button" className="arm-btn" onClick={onClose}>
            No, cancel
          </button>
          <button type="button" className="arm-btn arm-btn-go" disabled={!exact || busy} onClick={() => void go()}>
            {busy ? "Arming…" : "YES — I accept the disclaimer; arm for today"}
          </button>
        </>
      }
    >
      {note !== null ? (
        <div className="arm-blocker" role="alert">
          <b>Read it again.</b> {note}
        </div>
      ) : null}
      <div className="arm-chips">
        <Chip tone="ok">
          <span className="dot" /> ready to arm
        </Chip>
        {r.supervisorFresh !== null ? <Chip tone={r.supervisorFresh ? "ok" : "bad"}>supervisor {r.supervisorFresh ? "running" : "not running"}</Chip> : null}
        <Chip tone="ok">halt flag clear</Chip>
        {r.mode !== null ? <Chip tone={r.mode === "live" ? "bad" : "warn"}>{r.mode === "live" ? "LIVE: places real orders" : "dry-run: places nothing"}</Chip> : null}
      </div>
      <dl className="arm-facts">
        {r.facts.map((f) => (
          <div key={f.label}>
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      {r.warnings.length > 0 ? (
        <ul className="arm-warnings" aria-label="warnings">
          {r.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}
      {r.mode === "dry-run" && r.modeNote !== null ? <p className="muted arm-fine">{r.modeNote}</p> : null}
      <div className="arm-disclaimer">
        <pre tabIndex={0} aria-label="the disclaimer, in full">
          {r.disclaimer}
        </pre>
      </div>
      <div className="arm-type">
        <label htmlFor={inputId}>
          Type <span className="arm-yes">YES</span> in capitals to accept the disclaimer
        </label>
        <input
          ref={input}
          id={inputId}
          type="text"
          value={typed}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="YES"
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void go();
          }}
        />
        <span className="muted arm-fine">The box starts empty every time and is checked again on the server. Anything but YES does nothing.</span>
        <span className="arm-fine">After you accept, arming runs on the server. You can leave this page; a notice tells you when it is done.</span>
        {error !== null ? (
          <span className="pnl-neg" role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </Sheet>
  );
}

// ------------------------------------------------------------------------------ the disarm confirm

export function DisarmDialog({ loop, onClose }: { loop: string; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await postDisarm(loop);
      if (!out.ok) {
        setError(out.detail);
        return;
      }
      pushToast({ tone: "exit", title: `${loop} switched off for today`, message: out.detail, ttlMs: 8000 });
      void qc.invalidateQueries({ queryKey: LIVE_ARMING_KEY });
      void qc.invalidateQueries({ queryKey: ["live-today"] });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Sheet
      kick="Switch off"
      title={`Switch ${loop} off for today?`}
      sub="Disarming stops new entries. It places no order."
      onClose={onClose}
      foot={
        <>
          <button type="button" className="arm-btn" onClick={onClose}>
            No, keep it armed
          </button>
          <button type="button" className="arm-btn arm-btn-stop" disabled={busy} onClick={() => void go()}>
            {busy ? "Switching off…" : "Yes, switch it off"}
          </button>
        </>
      }
    >
      <p className="arm-fine">
        Open positions stay managed: fills are confirmed, resting stops are watched and the loop settles what it holds, until its own disarm cutoff. A resting entry order is not cancelled; a fill on it is
        still managed, or cancel it at the broker.
      </p>
      <p className="muted arm-fine">Arming again today needs a fresh confirmation.</p>
      {error !== null ? (
        <span className="pnl-neg" role="alert">
          {error}
        </span>
      ) : null}
    </Sheet>
  );
}
