import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { isModuleId } from "../lightbox/moduleOrder";

/**
 * A module name rendered as a link to its page (`/<module>`) when `id` names one, and as a plain
 * element otherwise -- several producers/rows carry an id with no page at all (streamer), and
 * linking those would be a dead click. Two shapes share this
 * same `isModuleId` gate: a table cell (`ModuleCellLink`, `module-link` styling) and a chip
 * (`ModuleChipLink`, kept as a chip element either way so its tone class still applies).
 */
export function ModuleCellLink({ id, children }: { id: string; children: ReactNode }) {
  return isModuleId(id) ? (
    <Link to={`/${id}`} className="module-link">
      {children}
    </Link>
  ) : (
    <>{children}</>
  );
}

export function ModuleChipLink({
  id,
  className,
  title,
  children,
}: {
  id: string;
  className: string;
  title?: string;
  children: ReactNode;
}) {
  return isModuleId(id) ? (
    <Link to={`/${id}`} className={className} title={title}>
      {children}
    </Link>
  ) : (
    <span className={className} title={title}>
      {children}
    </span>
  );
}
