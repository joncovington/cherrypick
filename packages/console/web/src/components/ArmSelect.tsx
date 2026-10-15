import { useQuery } from "@tanstack/react-query";
import type { ArmModule, RetiredArmsPayload } from "@console/shared";
import { ScopeSelect } from "./ScopeBar";
import { useBoolPref, writePref } from "../lib/prefs";

/** The console pref behind the "retired" toggle: one switch for every arm filter, kept on the server. */
export const SHOW_RETIRED_ARMS_PREF = "showRetiredArms";

/**
 * The arms a filter lists. Retired arms (the module's config says `enabled: false`) are hidden
 * unless asked for, but the arm already selected always stays, so hiding never silently changes
 * what the page is showing. Exported so the rule is pinned without a DOM.
 */
export function visibleArms(
  options: string[] | undefined,
  retired: readonly string[],
  showRetired: boolean,
  selected: string | null,
): string[] | undefined {
  if (options === undefined || showRetired || retired.length === 0) return options;
  const hide = new Set(retired);
  return options.filter((o) => !hide.has(o) || o === selected);
}

function useRetiredArms() {
  return useQuery<RetiredArmsPayload>({
    queryKey: ["arms-retired"],
    queryFn: async () => {
      const res = await fetch("/api/arms/retired");
      if (!res.ok) throw new Error(`retired arms: HTTP ${res.status}`);
      return (await res.json()) as RetiredArmsPayload;
    },
    staleTime: 5 * 60_000,
  });
}

/** An arm filter: ScopeSelect over the module's arms, retired ones hidden behind a toggle. */
export function ArmSelect({
  module,
  value,
  options,
  onChange,
  label = "arm filter",
  allLabel = "all arms",
}: {
  module: ArmModule;
  value: string | null;
  options: string[] | undefined;
  onChange: (v: string | null) => void;
  label?: string;
  allLabel?: string;
}) {
  const showRetired = useBoolPref(SHOW_RETIRED_ARMS_PREF);
  const retired = useRetiredArms().data?.retired[module] ?? [];
  const shown = visibleArms(options, retired, showRetired, value);
  const hidden = (options?.length ?? 0) - (shown?.length ?? 0);
  const retiredHere = (options ?? []).filter((o) => retired.includes(o)).length;
  return (
    <>
      <ScopeSelect label={label} value={value} options={shown} onChange={onChange} allLabel={allLabel} />
      {retiredHere > 0 && (
        <label
          className="muted lbl"
          style={{ fontSize: 11, whiteSpace: "nowrap" }}
          title={
            showRetired
              ? "Retired arms are listed. Untick to hide them."
              : `${hidden} retired ${hidden === 1 ? "arm is" : "arms are"} hidden. Tick to list them.`
          }
        >
          <input type="checkbox" checked={showRetired} onChange={(e) => void writePref(SHOW_RETIRED_ARMS_PREF, e.target.checked)} />{" "}
          retired
        </label>
      )}
    </>
  );
}
