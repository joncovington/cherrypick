/** Page-wide scope selectors — the shape every module dashboard leads with. */
export function ScopeSelect({
  label,
  value,
  options,
  onChange,
  allLabel = "all",
}: {
  label: string;
  value: string | null;
  options: string[] | undefined;
  onChange: (v: string | null) => void;
  allLabel?: string;
}) {
  return (
    <select
      className="text-input"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      aria-label={label}
      title={label}
    >
      <option value="">{allLabel}</option>
      {options?.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

/** A tab strip in the page title row, styled like the mode toggle. */
export function TabStrip<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: readonly T[];
  value: T;
  onChange: (t: T) => void;
}) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      {tabs.map((t) => (
        <button key={t} type="button" className={value === t ? "mode-btn active" : "mode-btn"} onClick={() => onChange(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

/** Loop freshness pill: LIVE when the module's loop wrote within its window. */
export function LoopPill({
  state,
  ageSeconds,
  detail,
}: {
  state: "live" | "idle" | "no-data" | undefined;
  ageSeconds: number | null | undefined;
  detail?: string;
}) {
  if (state === undefined) return null;
  const age =
    ageSeconds == null
      ? ""
      : ageSeconds < 90
        ? ` · ${Math.round(ageSeconds)}s ago`
        : ageSeconds < 5400
          ? ` · ${Math.round(ageSeconds / 60)}m ago`
          : ` · ${(ageSeconds / 3600).toFixed(1)}h ago`;
  const cls = state === "live" ? "chip-ok" : state === "idle" ? "chip-warn" : "chip-missing";
  return (
    <span className={`chip ${cls}`} title={detail}>
      {state === "live" ? "● loop live" : state === "idle" ? "◐ loop idle" : "○ no loop data"}
      {age}
    </span>
  );
}
