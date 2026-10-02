import { isExperimental } from "../lightbox/moduleOrder";

/**
 * The small "experimental" chip beside a module's name, wherever that name heads something (the
 * rail, the header menu, the page's breadcrumb). The list is `EXPERIMENTAL_MODULES`; nothing here
 * decides which modules are on it.
 */
export function ExperimentalChip({ id }: { id: string }) {
  if (!isExperimental(id)) return null;
  return (
    <span className="chip chip-experimental" title="An experimental module: a paper-only design still being shaped.">
      experimental
    </span>
  );
}
