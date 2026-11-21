import { Link } from "react-router-dom";
import type { ChangeRow, ChangesModule, PerformanceModuleId } from "@console/shared";
import { useModuleChanges } from "../../lib/api";
import { AnalysisSlide } from "../analysis/AnalysisSlide";
import { ChangesSection } from "../changes/ChangesSlide";
import { PerformanceSlide } from "../performance/PerformanceSlide";

/**
 * One arm's page for a module on the shared slides (`/<module>/arm/<name>`, framed as its arms tab):
 * against control (its changes row), what it did (the performance slide narrowed to the arm: its card,
 * its running net beside control's, its exit reasons and breaks) and its analysis. Each section is the
 * module's own tab with the arm fixed, so the page reads nothing of its own. MEIC has its own page
 * (`pages/Meic/MeicArmPage.tsx`) with its attempts, costs, regime cuts and history besides.
 */

export type ArmPageModule = PerformanceModuleId & ChangesModule;

export const ARM_JUMPS = [
  { id: "arm-control", label: "Against control" },
  { id: "arm-record", label: "What it did" },
  { id: "arm-analysis", label: "Analysis" },
] as const;

export function armHref(module: string, name: string): string {
  return `/${module}/arm/${encodeURIComponent(name)}`;
}

/** Whether `name` is a control: other arms are read against it and it has no row of its own (calendars
 *  keeps one per weekday, `friday:control`). Before the changes read lands, the name says. */
export function isControl(name: string, rows: ChangeRow[] | undefined): boolean {
  if (rows === undefined) return name === "control" || name.endsWith(":control");
  return rows.some((r) => r.against === name) && !rows.some((r) => r.arm === name);
}

/** What a base's page says: the arms read against it by name (a module's control, or the strategy an
 *  advisor experiment shadows), or that it is the control before the changes read lands. */
export function baseWords(name: string, rows: ChangeRow[] | undefined): string {
  const against = (rows ?? []).filter((r) => r.against === name).map((r) => r.name);
  if (against.length === 0) return "This is the control: the module's other arms are read against it.";
  return `Read against it: ${against.join(", ")}. It has no row of its own.`;
}

/** The arm drawn beside `name` on its page: what its own row is read against (`control` until the
 *  changes read lands), none for a control. */
export function referenceFor(name: string, rows: ChangeRow[] | undefined): string | null {
  if (isControl(name, rows)) return null;
  return rows?.find((r) => r.arm === name && r.kind !== "declared")?.against ?? "control";
}

export function ModuleArmPage({
  module,
  name,
  back = { to: `/${module}/arms`, label: "arms" },
}: {
  module: ArmPageModule;
  name: string;
  /** Where the crumbs lead back to: the arms tab, or the tab that lists the arms where there is none. */
  back?: { to: string; label: string };
}) {
  const changes = useModuleChanges(module);
  const rows = changes.data?.rows;
  const control = isControl(name, rows);
  // Drawn beside the arm: what its own row is read against, none for a control.
  const reference = referenceFor(name, rows);
  return (
    <div className="pf-layout arm-page">
      <nav className="grp-crumbs" aria-label="Where you are">
        <Link to={back.to}>{back.label}</Link>
        {" / "}
        <b>{name}</b>
      </nav>
      <nav className="ar-jump" aria-label="On this page">
        {ARM_JUMPS.map((j) => (
          <a key={j.id} href={`#${j.id}`}>
            {j.label}
          </a>
        ))}
      </nav>
      <div id="arm-control">
        {control ? (
          <p className="muted">{baseWords(name, rows)}</p>
        ) : (
          <ChangesSection module={module} arm={name} />
        )}
      </div>
      <div id="arm-record">
        <PerformanceSlide module={module} arm={name} pairs={false} reference={reference} />
      </div>
      <div id="arm-analysis">
        <AnalysisSlide module={module} arm={name} />
      </div>
    </div>
  );
}
