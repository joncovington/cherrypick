/**
 * The arm a group tag belongs to, and whether a tag passes the page's arm picker.
 *
 * Performance and analysis groups are tagged `<arm>@<experiment id>` for an advised book stamped
 * with its experiment (core.metrics' and core.analysis' bridges), and `<arm>` otherwise; regime
 * cuts and costs key the plain arm. Picking an arm keeps every experiment book run under it.
 */
export function armOfTag(tag: string): string {
  const at = tag.indexOf("@");
  return at < 0 ? tag : tag.slice(0, at);
}

/** True when `tag` belongs to the picked arm, or when no arm is picked. */
export function matchesArm(tag: string, arm: string | null | undefined): boolean {
  return arm === null || arm === undefined || armOfTag(tag) === arm;
}
