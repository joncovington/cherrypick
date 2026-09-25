import type { ReactNode } from "react";
import { NavLink, useSearchParams } from "react-router-dom";
import { type ModuleId } from "../../lightbox/moduleOrder";
import { NAV_DECL, navGroups } from "../../lightbox/navGroups";
import { SUITE_LINKS, MODULE_LINKS, CONFIG_LINK, type NavLinkDef } from "./navLinks";
import { useDirtyCount } from "../../pages/Config/stagedStore";

/**
 * The left rail: every page in the suite, with the current module opened out into its tabs.
 *
 * Only the open page expands. A tree with all eight open would be sixty-odd links, and the
 * rail's job is to make the current module's tabs one click away while keeping the rest one click
 * away too — not to show the whole suite at once.
 *
 * Built from `navGroups.ts` rather than the module's own manifest, so it renders server-side and
 * before the module's chunk has loaded. Flies' tabs are grouped (today / evidence / tables /
 * study / help) because fifteen flat entries is a list, not a structure; a module that declares
 * no groups gets one unlabelled run, which is what an unswept module wants.
 *
 * The query string rides along on every tab link: the header's arm/date/era selects live there,
 * and a tab change that silently dropped the reader's scope would be the same class of lie as a
 * stale date in a breadcrumb.
 */
export function ModuleNav({ module, slide }: { module: ModuleId; slide: string }) {
  const [params] = useSearchParams();
  const qs = params.toString();
  const withQs = (path: string) => (qs ? `${path}?${qs}` : path);
  const dirty = useDirtyCount();
  const grouping = navGroups(module, NAV_DECL[module].slides);

  // Any page opens out in place, wherever its link sits: a suite surface (Reports, Live, Config)
  // is on the frame like a module since 2026-09-25, and its tabs belong under its own name.
  const link = (l: NavLinkDef, extra?: ReactNode) => {
    if (l.to.slice(1) !== module) {
      return (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => (isActive ? "mf-nav-link active" : "mf-nav-link")}
        >
          {l.label}
          {extra}
        </NavLink>
      );
    }
    return (
      <div key={l.to} className="mf-nav-open">
        {/* The current page is a heading, not a link — every tab under it already is one, and a
            link to where you are is a wasted tab stop. */}
        <div className="mf-nav-link mf-nav-current">
          {l.label}
          {extra}
        </div>
        {grouping.groups.map(
          (g) =>
            g.slides.length > 0 && (
              <div key={g.label ?? "_"}>
                {g.label !== null && <div className="mf-nav-group">{g.label}</div>}
                {g.slides.map((sl) => (
                  <NavLink
                    key={sl.id}
                    to={withQs(`/${module}/${sl.id}`)}
                    className={sl.id === slide ? "mf-nav-slide active" : "mf-nav-slide"}
                    aria-current={sl.id === slide ? "page" : undefined}
                  >
                    {sl.label}
                  </NavLink>
                ))}
              </div>
            ),
        )}
      </div>
    );
  };

  return (
    <nav className="mf-nav" aria-label="modules">
      {SUITE_LINKS.map((l) => link(l))}
      <div className="mf-nav-section">Modules</div>
      {MODULE_LINKS.map((l) => link(l))}
      <div className="mf-nav-section">Suite</div>
      {link(
        CONFIG_LINK,
        dirty > 0 && <span className="nav-dot" title={`${String(dirty)} unsaved change${dirty === 1 ? "" : "s"}`} />,
      )}
    </nav>
  );
}
