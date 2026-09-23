import { NavLink, useSearchParams } from "react-router-dom";
import { type ModuleId } from "../../lightbox/moduleOrder";
import { NAV_DECL, navGroups } from "../../lightbox/navGroups";
import { SUITE_LINKS, MODULE_LINKS, CONFIG_LINK } from "./navLinks";
import { useDirtyCount } from "../../pages/Config/stagedStore";

/**
 * The left rail: every page in the suite, with the current module opened out into its tabs.
 *
 * Only the active module expands. A tree with all eight open would be sixty-odd links, and the
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
  const decl = NAV_DECL[module];
  const grouping = decl === undefined ? null : navGroups(module, decl.slides);

  return (
    <nav className="mf-nav" aria-label="modules">
      {SUITE_LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => (isActive ? "mf-nav-link active" : "mf-nav-link")}
        >
          {l.label}
        </NavLink>
      ))}

      <div className="mf-nav-section">Modules</div>
      {MODULE_LINKS.map((l) => {
        const id = l.to.slice(1);
        if (id !== module) {
          return (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) => (isActive ? "mf-nav-link active" : "mf-nav-link")}
            >
              {l.label}
            </NavLink>
          );
        }
        return (
          <div key={l.to} className="mf-nav-open">
            {/* The current module is a heading, not a link — every tab under it already is one,
                and a link to where you are is a wasted tab stop. */}
            <div className="mf-nav-link mf-nav-current">{l.label}</div>
            {grouping?.groups.map(
              (g) =>
                g.slides.length > 0 && (
                  <div key={g.label ?? "_"}>
                    {g.label !== null && <div className="mf-nav-group">{g.label}</div>}
                    {g.slides.map((s) => (
                      <NavLink
                        key={s.id}
                        to={withQs(`/${module}/${s.id}`)}
                        className={
                          s.id === slide ? "mf-nav-slide active" : "mf-nav-slide"
                        }
                        aria-current={s.id === slide ? "page" : undefined}
                      >
                        {s.label}
                      </NavLink>
                    ))}
                  </div>
                ),
            )}
          </div>
        );
      })}

      <div className="mf-nav-section">Suite</div>
      <NavLink
        to={CONFIG_LINK.to}
        className={({ isActive }) => (isActive ? "mf-nav-link active" : "mf-nav-link")}
      >
        {CONFIG_LINK.label}
        {dirty > 0 && (
          <span className="nav-dot" title={`${String(dirty)} unsaved change${dirty === 1 ? "" : "s"}`} />
        )}
      </NavLink>
    </nav>
  );
}
