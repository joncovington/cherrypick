import { NavLink } from "react-router-dom";
import { useDirtyCount } from "../../pages/Config/stagedStore";

/**
 * Grouped rather than flat: eleven equal-weight links read as one undifferentiated list, and the
 * page you want is found by scanning all of them. The groups say what kind of surface each link is —
 * the suite as a whole, one module's read models, the research tools, and the one page that writes.
 */
const groups: Array<{ label: string | null; links: Array<{ to: string; label: string; end?: boolean }> }> = [
  { label: null, links: [{ to: "/", label: "Overview", end: true }, { to: "/review", label: "Review" }] },
  {
    label: "Modules",
    links: [
      { to: "/meic", label: "MEIC" },
      { to: "/flies", label: "Flies" },
      { to: "/earnings", label: "Earnings" },
      { to: "/champions", label: "Champions" },
      { to: "/gex", label: "GEX" },
    ],
  },
  {
    label: "Research",
    links: [
      { to: "/scout", label: "Watchlist", end: true },
      { to: "/scout/screener", label: "Screener" },
      { to: "/scout/builder", label: "Builder" },
      { to: "/scout/orders", label: "Orders" },
    ],
  },
  { label: "Suite", links: [{ to: "/config", label: "Config" }] },
];

export function Nav() {
  // Staged config edits survive navigating away, so the nav has to say they are still waiting —
  // otherwise leaving the page looks identical to having saved.
  const dirty = useDirtyCount();
  return (
    <nav className="nav">
      <div className="nav-brand">cherrypick</div>
      {groups.map((group, i) => (
        <div className="nav-group" key={group.label ?? `group-${String(i)}`}>
          {group.label !== null && <div className="nav-group-label">{group.label}</div>}
          {group.links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {l.label}
              {l.to === "/config" && dirty > 0 && (
                <span className="nav-dot" title={`${String(dirty)} unsaved change${dirty === 1 ? "" : "s"}`} />
              )}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}
