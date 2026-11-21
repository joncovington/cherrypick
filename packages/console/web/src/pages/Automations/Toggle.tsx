/**
 * The automations pages' switch: a sliding toggle that says ON or OFF in its track, the one control every
 * on/off on these pages uses (a group's, an automation's own, retired). `pending` marks a change staged but
 * not saved (a dot on its corner). `tone` "off" keeps a switch that is not good news when on (retired)
 * from reading as one. A retired automation's own switch is never a toggle: `LockedSwitch` stands in.
 */
export function Toggle({
  on,
  label,
  onChange,
  big = false,
  pending = false,
  tone = "ok",
  disabled = false,
}: {
  on: boolean;
  label: string;
  onChange: (on: boolean) => void;
  big?: boolean;
  pending?: boolean;
  tone?: "ok" | "off";
  disabled?: boolean;
}) {
  const cls = ["tgl", big ? "tgl-big" : "", tone === "off" ? "tgl-grey" : "", pending ? "is-pending" : ""].filter(Boolean).join(" ");
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={cls} disabled={disabled} onClick={() => onChange(!on)}>
      <span className="tgl-word">{on ? "ON" : "OFF"}</span>
      <span className="tgl-knob" aria-hidden="true" />
    </button>
  );
}

/** A retired automation's switch: not a control, a label saying why it cannot be switched on here. */
export function LockedSwitch({ title = "Reinstate it on its own page to switch it on" }: { title?: string }) {
  return (
    <span className="tgl-locked" title={title}>
      RETIRED
    </span>
  );
}
