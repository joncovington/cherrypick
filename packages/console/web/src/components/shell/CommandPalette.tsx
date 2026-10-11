import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { NAV_DECL } from "../../lightbox/navGroups";
import { MODULE_LABEL, isModuleId, type ModuleId } from "../../lightbox/moduleOrder";
import { CONFIG_LINK, visibleNavLinks } from "./navLinks";
import { useFeatures } from "../../lib/useFeatures";
import { isSlideVisible } from "../../lib/visibility";
import { usePaletteOpen, setPaletteOpen } from "./paletteStore";

/**
 * The command palette — the keyboard-driven jump-to-anything dialog, opened with Ctrl/Cmd+K (or
 * `?`), on every page.
 *
 * It exists because the header's hamburger menu used to carry two jobs at once: page navigation
 * AND the shortcut legend (`g o`, `1`–`8` printed beside each link). When the rail became the one
 * nav on every page (2026-10-11), the menu went, and with it the only place the shortcuts were
 * written down — and a hint that is nowhere visible is worse than no hint. The legend moves here,
 * and becomes searchable while it moves: this is the one surface where every page and the current
 * module's tabs are named, filtered, and reachable without the mouse.
 *
 * The list is built from what the suite declares — `visibleNavLinks` (the rail's own list) plus
 * the current module's slides from `navGroups.ts` — never a hand-kept copy; an off module is not
 * offered, and a module the suite turns off while the palette is open loses its rows on the next
 * render, exactly as the rail does.
 */

interface PaletteItem {
  /** Stable key: the destination path, or `tab:<module>/<slide>`. */
  id: string;
  label: string;
  /** Where the pick navigates; tab rows carry the page's query string. */
  to: string;
  /** Group heading rendered once above the item's run. */
  group: string;
  /** The shortcut hint, verbatim as documented (`g o`, `2`). Searchable. */
  hint?: string;
}

/** The suite pages and Config, in the rail's order, each carrying its shortcut hint. */
function staticItems(features: ReturnType<typeof useFeatures>): PaletteItem[] {
  const { suite, modules } = visibleNavLinks(features);
  return [
    ...suite.map((l) => ({
      id: l.to,
      label: l.label,
      to: l.to,
      group: "suite pages",
      // Suite pages are `g`-chords (`g o`, `g r`); the digits are bare and name the modules.
      hint: l.key !== undefined ? `g ${l.key}` : undefined,
    })),
    ...modules.map((l) => ({
      id: l.to,
      label: l.label,
      to: l.to,
      group: "modules",
      hint: l.key,
    })),
    { id: CONFIG_LINK.to, label: CONFIG_LINK.label, to: CONFIG_LINK.to, group: "suite" },
  ];
}

export function CommandPalette() {
  const open = usePaletteOpen();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const qs = params.toString();
  const features = useFeatures();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // The current module's slides, so `/flies` + palette names "forest" and lands on it with the
  // page's query (mode, date, arm, era) riding along — the same contract the rail's tabs have.
  const seg = location.pathname.split("/")[1] ?? "";
  const module = isModuleId(seg) ? (seg as ModuleId) : null;
  const slideIds = module === null ? [] : NAV_DECL[module].slides.filter((s) => isSlideVisible(module, s.id, features)).map((s) => s.id);

  const items = useMemo<PaletteItem[]>(() => {
    const base = staticItems(features);
    if (module === null || slideIds.length === 0) return base;
    const declared = NAV_DECL[module].slides.filter((s) => slideIds.includes(s.id));
    const tabRows: PaletteItem[] = declared.map((s) => ({
      id: `tab:${module}/${s.id}`,
      label: s.label,
      to: qs ? `/${module}/${s.id}?${qs}` : `/${module}/${s.id}`,
      group: `${MODULE_LABEL[module]} tabs`,
    }));
    return [...tabRows, ...base];
    // The slide ids string is the real dependency; `declared` re-derives from it deterministically.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features, module, qs, slideIds.join(",")]);

  // Substring filter, case-folded; a match on the start of the label ranks first so "fl" names
  // Flies before it names a tab. The hint text is searchable too — "g r" finds Reports.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return items;
    const scored = items.map((it) => {
      const label = it.label.toLowerCase();
      const rank = label.startsWith(q) ? 0 : label.includes(q) ? 1 : (it.hint ?? "").toLowerCase().includes(q) ? 2 : -1;
      return { it, rank };
    });
    return scored
      .filter((s) => s.rank >= 0)
      .sort((a, b) => a.rank - b.rank)
      .map((s) => s.it);
  }, [items, query]);

  // Reset the query and selection each time the palette OPENS (not on every keystroke); remember
  // the opener's focus so Escape hands it back — the menu used to do the same for its trigger.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected(0);
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // The input exists one paint after `open` flips; focus on the next frame.
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  // A navigation under the palette (a keyboard chord, the rail) closes it — the same rule the
  // hamburger menu closed itself on.
  useEffect(() => {
    setPaletteOpen(false);
  }, [location.pathname]);

  // Keep the selected row in view when the arrows move it.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${String(selected)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (!open) return null;

  const pick = (it: PaletteItem | undefined) => {
    if (it === undefined) return;
    setPaletteOpen(false);
    navigate(it.to);
  };

  const close = () => {
    setPaletteOpen(false);
    restoreFocusRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, filtered.length - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      pick(filtered[selected]);
      return;
    }
    // Ctrl/Cmd+K toggles, even from inside the palette's own input.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  };

  let lastGroup: string | null = null;

  return (
    <div
      className="cmdk-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="command palette">
        <input
          ref={inputRef}
          className="cmdk-input"
          type="text"
          placeholder="jump to a page or tab…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={onKeyDown}
          aria-label="search pages"
          autoComplete="off"
          spellCheck={false}
        />
        <div className="cmdk-list" ref={listRef} role="listbox" aria-label="pages">
          {filtered.length === 0 && <div className="cmdk-empty">nothing matches “{query}”</div>}
          {filtered.map((it, i) => {
            const groupHeader = it.group !== lastGroup ? it.group : null;
            lastGroup = it.group;
            return (
              <div key={it.id}>
                {groupHeader !== null && <div className="cmdk-group">{groupHeader}</div>}
                {/* A real link, not a div-with-onClick: the row is href-visible to a server
                    render (what the tests and any reader's copy-link see), middle-clickable, and
                    the pick still goes through `pick` so the palette closes first. */}
                <NavLink
                  to={it.to}
                  data-idx={String(i)}
                  role="option"
                  aria-selected={i === selected}
                  className={i === selected ? "cmdk-item active" : "cmdk-item"}
                  onMouseEnter={() => setSelected(i)}
                  // A mousedown-prevented item keeps focus in the input, so the palette never
                  // blurs mid-pick.
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => {
                    e.preventDefault();
                    pick(it);
                  }}
                >
                  <span className="cmdk-label">{it.label}</span>
                  {it.hint !== undefined && <span className="cmdk-kbd">{it.hint}</span>}
                </NavLink>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}
