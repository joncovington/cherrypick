import { DataTab, EnvironmentTab, HealthTab, LogsTab, ModulesTab, SupervisorTab } from "../../pages/System/SystemTabs";
import { useSystemHealth } from "../../lib/api";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The System page (2026-10-01): the suite's own health, read-only, one subsystem a tab. `health` is
 * the verdict at a glance and the rest are the detail behind it. No control path: restarting,
 * holding or arming anything stays with `run.py` and the arm commands.
 */
const slides: SlideDef[] = [
  { id: "health", label: "health", render: () => <HealthTab /> },
  { id: "supervisor", label: "supervisor", render: () => <SupervisorTab /> },
  { id: "modules", label: "modules", render: () => <ModulesTab /> },
  { id: "data", label: "data", render: () => <DataTab /> },
  { id: "environment", label: "environment", render: () => <EnvironmentTab /> },
  { id: "logs", label: "logs", render: () => <LogsTab /> },
];

/** The worst tile's level, in the header on every tab. */
function HealthBadge() {
  const { data } = useSystemHealth();
  if (data === undefined) return null;
  const levels = data.checks.map((c) => c.level);
  const worst = levels.includes("critical") ? "critical" : levels.includes("warn") ? "warn" : levels.includes("unknown") ? "unknown" : "ok";
  const bad = data.checks.filter((c) => c.level === "critical" || c.level === "warn").map((c) => c.label);
  const cls = { ok: "chip chip-ok", warn: "chip chip-warn", critical: "chip chip-missing", unknown: "chip" }[worst];
  return (
    <span className={cls} title={bad.length > 0 ? `attention: ${bad.join(", ")}` : "every subsystem reports OK"}>
      {worst === "ok" ? "all OK" : bad.length > 0 ? `${String(bad.length)} need attention` : "partly unknown"}
    </span>
  );
}

export function SystemLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="system" slide={slide} slides={slides} session={null} badge={<HealthBadge />} />;
}
