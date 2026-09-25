import { useEffect, useRef, useState } from "react";
import { resolveColumns, type ColumnDef, type ColumnLayout } from "./columns";

/**
 * The "columns" control on a history table: a small panel dropped from a header button, not a
 * dialog -- the table stays visible and updates live behind it, and a click outside or Escape closes
 * it. Describe columns can be hidden and dragged (or moved with the arrow buttons, for keyboards)
 * into any order; money columns can be hidden but keep the standard's order; `net` is always shown.
 */
export function ColumnsMenu<R>({
  defs,
  layout,
  onChange,
  onReset,
  isDefault,
}: {
  defs: ColumnDef<R>[];
  layout: ColumnLayout;
  onChange: (next: ColumnLayout) => void;
  onReset: () => void;
  isDefault: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current !== null && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const byId = new Map(defs.map((d) => [d.id, d]));
  // The describe order as it resolves today, hidden ones included, so the list matches the table.
  const describeOrder = resolveColumns(defs, { ...layout, hidden: [] })
    .filter((d) => d.kind === "describe")
    .map((d) => d.id);
  const money = defs.filter((d) => d.kind === "money");
  const hidden = new Set(layout.hidden);
  const shownCount = defs.length - defs.filter((d) => hidden.has(d.id) && d.pinned !== true).length;

  const toggle = (id: string) => {
    const next = hidden.has(id) ? layout.hidden.filter((h) => h !== id) : [...layout.hidden, id];
    onChange({ order: describeOrder, hidden: next });
  };
  const move = (id: string, to: number) => {
    const order = describeOrder.filter((x) => x !== id);
    order.splice(Math.max(0, Math.min(order.length, to)), 0, id);
    onChange({ order, hidden: layout.hidden });
  };

  return (
    <div className="cols-menu" ref={ref}>
      <button
        type="button"
        className={`mode-btn ${open ? "active" : ""}`}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
        title="show, hide and reorder this table's columns"
      >
        columns {shownCount}/{defs.length} ▾
      </button>
      {open && (
        <div className="cols-panel" role="group" aria-label="table columns">
          <div className="cols-section">describe · drag to reorder</div>
          <ul className="cols-list">
            {describeOrder.map((id, i) => {
              const d = byId.get(id)!;
              return (
                <li
                  key={id}
                  className={`cols-item ${dragging === id ? "dragging" : ""}`}
                  draggable
                  onDragStart={(e) => {
                    setDragging(id);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragging !== null && dragging !== id) move(dragging, i);
                    setDragging(null);
                  }}
                  onDragEnd={() => setDragging(null)}
                >
                  <span className="cols-grip" aria-hidden="true">⋮⋮</span>
                  <label>
                    <input type="checkbox" checked={!hidden.has(id)} onChange={() => toggle(id)} />
                    {d.header === "" ? d.id : d.header}
                  </label>
                  <span className="cols-arrows">
                    <button type="button" aria-label={`move ${d.header || d.id} left`} disabled={i === 0} onClick={() => move(id, i - 1)}>
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label={`move ${d.header || d.id} right`}
                      disabled={i === describeOrder.length - 1}
                      onClick={() => move(id, i + 1)}
                    >
                      ↓
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="cols-section" title="The money block stays in the standard's order so each row adds up left to right, and net is always last.">
            money · fixed order, net always shown
          </div>
          <ul className="cols-list">
            {money.map((d) => (
              <li key={d.id} className="cols-item">
                <label title={d.title}>
                  <input
                    type="checkbox"
                    checked={d.pinned === true || !hidden.has(d.id)}
                    disabled={d.pinned === true}
                    onChange={() => toggle(d.id)}
                  />
                  {d.header}
                </label>
              </li>
            ))}
          </ul>
          <button type="button" className="mode-btn cols-reset" disabled={isDefault} onClick={onReset}>
            reset to standard
          </button>
        </div>
      )}
    </div>
  );
}
