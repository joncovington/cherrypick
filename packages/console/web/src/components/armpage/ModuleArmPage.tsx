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

/** The arm drawn beside `name` on its page: what its own row is read against (`control` until the
 *  changes read lands), none for a control. */
export function referenceFor(name: string, rows: ChangeRow[] | undefined): string | null {
  if (isControl(name, rows)) return null;
  return rows?.find((r) => r.arm === name && r.kind !== "declared")?.against ?? "control";
}

export function ModuleArmPage({ module, name }: { module: ArmPageModule; name: string }) {
  const changes = useModuleChanges(module);
  const rows = changes.data?.rows;
  const control = isControl(name, rows);
  // Drawn beside the arm: what its own row is read against, none for a control.
  const reference = referenceFor(name, rows);
  return (
    <div className="pf-layout arm-page">
      <nav className="grp-crumbs" aria-label="Where you are">
        <Link to={`/${module}/arms`}>arms</Link>
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
          <p className="muted">This is the control: every other arm is read against it.</p>
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
