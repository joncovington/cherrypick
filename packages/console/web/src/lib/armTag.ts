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

/** The experiment id a tag carries (`<arm>@<experiment id>`), or null. */
export function experimentOfTag(tag: string): string | null {
  const at = tag.indexOf("@");
  return at < 0 ? null : tag.slice(at + 1);
}

/** What a group is called on a card or a chart line: its arm, and the full tag only where two groups
 *  shown together would share an arm (two experiment books under one name). The id goes beneath. */
export function displayName(tag: string, all: readonly string[]): string {
  const arm = armOfTag(tag);
  return all.filter((t) => armOfTag(t) === arm).length > 1 ? tag : arm;
}

/** True when `tag` belongs to the picked arm, or when no arm is picked. */
export function matchesArm(tag: string, arm: string | null | undefined): boolean {
  return arm === null || arm === undefined || armOfTag(tag) === arm;
}
