import type { ReactNode } from "react";
import { getPref, usePrefsVersion, writePref } from "../../lib/prefs";

/**
 * A history table's columns, declared once: header, definition, and cell. Hiding and reordering
 * come from this list rather than from hand-written `<th>`/`<td>` rows, so every history table
 * gets the same controls and a column's definition travels with it.
 *
 * Two kinds, and the difference is the trade table standard (root CLAUDE.md), not taste:
 *
 * - `describe` columns -- dates, strikes, arm, exit reason -- say which trade a row is. Any of
 *   them can be hidden and they can be put in any order.
 * - `money` columns -- entry, exit, gross, fees, settle, slip, net -- are the row adding up, left
 *   to right. They can be hidden, but they stay one block in the standard's order at the end of
 *   the table, and `net` is always shown and always last: a history without its net, or with the
 *   net in the middle, no longer reads as the standard.
 *
 * The layout is a per-viewer preference, kept in the console's prefs store (a synchronous local
 * mirror, synced to the server so it follows to the desktop shell), keyed per table.
 */

export interface ColumnDef<R> {
  id: string;
  header: string;
  /** The column's definition, shown on its header. */
  title?: string;
  kind: "describe" | "money";
  /** Only `net`: cannot be hidden, always last. */
  pinned?: boolean;
  /** A describe column holding numbers (strikes, qty, price): right-aligned. Money always is. */
  numeric?: boolean;
  className?: string;
  render: (row: R) => ReactNode;
}

export interface ColumnLayout {
  /** Describe-column ids in display order. */
  order: string[];
  hidden: string[];
}

function isLayout(v: unknown): v is ColumnLayout {
  const o = v as ColumnLayout | undefined;
  return o !== undefined && o !== null && Array.isArray(o.order) && Array.isArray(o.hidden);
}

/** The default layout: declaration order, nothing hidden. */
export function defaultLayout<R>(defs: ColumnDef<R>[]): ColumnLayout {
  return { order: defs.filter((d) => d.kind === "describe").map((d) => d.id), hidden: [] };
}

/**
 * The columns to render, in order. Pure, so it can be tested without a DOM.
 *
 * A stored layout is reconciled with the declaration rather than trusted: an id it names that no
 * longer exists is dropped, and a column declared since it was saved appears in its declared
 * position -- a column added to the table must not be invisible to everyone who once reordered.
 */
export function resolveColumns<R>(defs: ColumnDef<R>[], layout: ColumnLayout): ColumnDef<R>[] {
  const byId = new Map(defs.map((d) => [d.id, d]));
  const hidden = new Set(layout.hidden);
  const describe = defs.filter((d) => d.kind === "describe");
  const known = layout.order.filter((id) => byId.get(id)?.kind === "describe");
  const ordered = [...known];
  describe.forEach((d, i) => {
    if (ordered.includes(d.id)) return;
    // Insert after the nearest declared predecessor that is present.
    const before = describe.slice(0, i).reverse().find((p) => ordered.includes(p.id));
    ordered.splice(before === undefined ? 0 : ordered.indexOf(before.id) + 1, 0, d.id);
  });
  const money = defs.filter((d) => d.kind === "money");
  const unpinned = money.filter((d) => d.pinned !== true);
  const pinned = money.filter((d) => d.pinned === true);
  return [
    ...ordered.map((id) => byId.get(id)!).filter((d) => !hidden.has(d.id)),
    ...unpinned.filter((d) => !hidden.has(d.id)),
    ...pinned,
  ];
}

export function useColumnLayout<R>(
  table: string,
  defs: ColumnDef<R>[],
): {
  layout: ColumnLayout;
  columns: ColumnDef<R>[];
  setLayout: (next: ColumnLayout) => void;
  reset: () => void;
  isDefault: boolean;
} {
  usePrefsVersion();
  const key = `columns:${table}`;
  const stored = getPref(key);
  const layout = isLayout(stored) ? stored : defaultLayout(defs);
  const columns = resolveColumns(defs, layout);
  const setLayout = (next: ColumnLayout) => {
    void writePref(key, next);
  };
  return {
    layout,
    columns,
    setLayout,
    reset: () => {
      void writePref(key, null);
    },
    isDefault: !isLayout(stored),
  };
}
