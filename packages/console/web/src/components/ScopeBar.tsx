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

/**
 * Era selector. Unlike the other scope selects, its default is not "everything"
 * — it is the module's current era, matching what the module's own analytics
 * count as evidence. Earlier eras stay reachable, and picking one says so out
 * loud rather than quietly mixing shakedown rows into the numbers.
 */
export function EraSelect({
  value,
  eras,
  currentEra,
  onChange,
}: {
  value: string | null;
  eras: Array<{ era: string; trades: number }> | undefined;
  currentEra: string | undefined;
  onChange: (v: string | null) => void;
}) {
  if (eras === undefined || eras.length < 2) return null;
  const total = eras.reduce((s, e) => s + e.trades, 0);
  const currentCount = eras.find((e) => e.era === currentEra)?.trades ?? 0;
  return (
    <select
      className={`text-input ${value === null ? "" : "scope-select-off-default"}`}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      aria-label="era"
      title="which era of trades counts as evidence"
    >
      <option value="">era {currentEra} · {currentCount}</option>
      {eras
        .filter((e) => e.era !== currentEra)
        .map((e) => (
          <option key={e.era} value={e.era}>
            era {e.era} · {e.trades}
          </option>
        ))}
      <option value="ALL">every era · {total}</option>
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
