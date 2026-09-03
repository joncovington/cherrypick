import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useDirtyCount } from "../../pages/Config/stagedStore";

/**
 * Page navigation as a hamburger dropdown on the "cherrypick" brand, replacing the fixed-width
 * side nav (2026-09). The nav column cost every page 11rem of a viewport the Overview redesign
 * needs back to fit 1440×900 without scrolling; a menu that opens over the content rather than
 * beside it gives that width back everywhere, not just on Overview.
 *
 * Same three groups the old `Nav` held, same active-route styling, same Config dirty dot -- this
 * is a relocation of that component's data, not a new information architecture.
 */
const groups: Array<{ label: string | null; links: Array<{ to: string; label: string; end?: boolean; key?: string }> }> = [
  {
    label: null,
    links: [
      { to: "/", label: "Overview", end: true, key: "o" },
      { to: "/reports", label: "Reports", key: "r" },
      { to: "/advisor", label: "Advisor", key: "a" },
    ],
  },
  {
    label: "Modules",
    links: [
      { to: "/meic", label: "MEIC", key: "1" },
      { to: "/flies", label: "Flies", key: "2" },
      { to: "/pmcc", label: "PMCC", key: "3" },
      { to: "/curve", label: "Curve", key: "4" },
      { to: "/bwb", label: "BWB", key: "5" },
      { to: "/calendars", label: "Calendars", key: "6" },
      { to: "/earnings", label: "Earnings", key: "7" },
      { to: "/gex", label: "GEX", key: "8" },
    ],
  },
  { label: "Suite", links: [{ to: "/config", label: "Config" }] },
];

const ALL_LINKS = groups.flatMap((g) => g.links);

function currentLabel(pathname: string): string {
  // Longest-match: "/meic" must not match before a more specific future route under it does.
  const hit = [...ALL_LINKS].sort((a, b) => b.to.length - a.to.length).find((l) => {
    return l.end === true ? pathname === l.to : pathname === l.to || pathname.startsWith(`${l.to}/`);
  });
  return hit?.label ?? "Overview";
}

export function HeaderMenu() {
  const [open, setOpen] = useState(false);
  const dirty = useDirtyCount();
  const location = useLocation();
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current !== null && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Closing on navigation is what makes clicking a link actually leave the menu -- without this
  // the dropdown stays open, floating over the new page, until the next outside click.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <div className="header-menu" ref={rootRef}>
      <button
        type="button"
        ref={buttonRef}
        className="header-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
          <line x1="2" y1="4" x2="14" y2="4" />
          <line x1="2" y1="8" x2="14" y2="8" />
          <line x1="2" y1="12" x2="14" y2="12" />
        </svg>
        <span className="header-menu-brand">cherrypick</span>
        <span className="header-menu-current muted">{currentLabel(location.pathname)} ▾</span>
      </button>
      {open && (
        <nav className="header-menu-dropdown" role="menu" aria-label="pages">
          {groups.map((group, i) => (
            <div className="header-menu-group" key={group.label ?? `group-${String(i)}`}>
              {group.label !== null && <div className="header-menu-group-label">{group.label}</div>}
              {group.links.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  end={l.end}
                  role="menuitem"
                  className={({ isActive }) => (isActive ? "header-menu-link active" : "header-menu-link")}
                >
                  {l.label}
                  {l.key !== undefined && <span className="header-menu-kbd">g {l.key}</span>}
                  {l.to === "/config" && dirty > 0 && (
                    <span className="nav-dot" title={`${String(dirty)} unsaved change${dirty === 1 ? "" : "s"}`} />
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      )}
    </div>
  );
}
