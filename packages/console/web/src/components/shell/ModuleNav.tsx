import type { ReactNode } from "react";
import { NavLink, useSearchParams } from "react-router-dom";
import { type ModuleId, isExperimental } from "../../lightbox/moduleOrder";
import { NAV_DECL, navGroups, viewSlides } from "../../lightbox/navGroups";
import { useViewMode } from "../../lib/viewMode";
import { CONFIG_LINK, railHalves, suiteLinksFor, visibleNavLinks, type NavLinkDef } from "./navLinks";
import { useFeatures } from "../../lib/useFeatures";
import { isSlideVisible } from "../../lib/visibility";
import { useLiveLoopsInUse } from "../../lib/useLiveToday";
import { ExperimentalChip } from "../ExperimentalChip";
import { useDirtyCount } from "../../pages/Config/stagedStore";
import { RAIL_PREF, useCloseRailOnNavigate, useRailExpanded } from "./railState";
import { NavIcon } from "./navIcons";

export { RAIL_PREF };

/** A rail link's tooltip with its keyboard shortcut (`lib/keyboardNav.ts`): `g` then the key for a
 *  suite page, the bare digit for a module. The rail is the only place the hints are shown. */
export function shortcutTitle(l: NavLinkDef, isModule: boolean): string {
  if (l.key === undefined) return l.label;
  return `${l.label} (${isModule ? l.key : `g ${l.key}`})`;
}

/**
 * The left rail: every page in the suite, with the current module opened out into its tabs.
 *
 * Mounted ONCE, in the shell (2026-10-11) — every page has it, Overview included — replacing the
 * per-frame copy and the header's hamburger menu, which had become the second and third listing of
 * the same pages. One nav, one source of truth (`navLinks.ts`).
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
 *
 * Collapsed by default to a 64px icon strip, which gives the page the width. The links stay in
 * the document under `hidden` rather than unmounting, so they are out of sight and out of the
 * tab order but the rail's structure is the same either way. Collapsing is ONLY the rail's own
 * toggle: the top bar holds no menu and no second trigger.
 *
 * `module` is the current page's segment or null (the Overview): null opens no tabs out, and the
 * rail is a flat list — which is exactly the Overview's old relationship to it.
 */
export function ModuleNav({ module, slide }: { module: ModuleId | null; slide: string }) {
  const [expanded, toggleRail] = useRailExpanded();
  useCloseRailOnNavigate();
  const [params] = useSearchParams();
  const qs = params.toString();
  const withQs = (path: string) => (qs ? `${path}?${qs}` : path);
  const dirty = useDirtyCount();
  // An off module, the advisor and its tabs, and the technicals chart leave the rail when the suite has
  // turned them off (`visibility.ts`); Config keeps every toggle, so it is never filtered.
  const features = useFeatures();
  const liveInUse = useLiveLoopsInUse(features);
  // The open module's tabs in the reader's view (simple or detail); the tab they are on always stays.
  // Simple view also leaves the developer surfaces (Compare, Advisor, System) off the rail.
  const view = useViewMode();
  const visible = visibleNavLinks(features);
  const suite = suiteLinksFor(visible.suite, view, module);
  const { modules } = visible;
  // Real money and the market reads above the modules; research and the suite's own pages below.
  const { above, below } = railHalves(suite);
  const research = below.filter((l) => l.section === "research");
  const suitePages = below.filter((l) => l.section !== "research");
  const grouping = navGroups(
    module ?? ("meic" as ModuleId), // never read when module is null — grouping is built only for the open module
    module === null
      ? []
      : viewSlides(module, NAV_DECL[module].slides.filter((s) => isSlideVisible(module, s.id, features, liveInUse)), view, slide),
  );

  // Any page opens out in place, wherever its link sits: a suite surface (Reports, Live, Config)
  // is on the frame like a module since 2026-09-25, and its tabs belong under its own name.
  const link = (l: NavLinkDef, extra?: ReactNode) => {
    const title = shortcutTitle(l, modules.includes(l));
    // Icon FIRST, in a fixed column (`.mf-nav-ic`): its glyph centre matches the collapsed
    // strip's, so folding the rail hides the label without the icon moving. Was trailing the
    // variable-width label, which left the glyphs a ragged column and made them jump on collapse.
    const icon = (
      <span className="mf-nav-ic" aria-hidden="true">
        <NavIcon to={l.to} />
      </span>
    );
    const trailing = (
      <>
        <ExperimentalChip id={l.to.slice(1)} />
        {extra}
      </>
    );
    if (module === null || l.to.slice(1) !== module) {
      return (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          title={title}
          className={({ isActive }) => (isActive ? "mf-nav-link active" : "mf-nav-link")}
        >
          {icon}
          {l.label}
          {trailing}
        </NavLink>
      );
    }
    return (
      <div key={l.to} className="mf-nav-open">
        {/* The current page is a heading, not a link — every tab under it already is one, and a
            link to where you are is a wasted tab stop. */}
        <div className="mf-nav-link mf-nav-current">
          {icon}
          {l.label}
          {trailing}
        </div>
        {grouping.groups.map(
          (g) =>
            g.slides.length > 0 && (
              <div key={g.label ?? "_"}>
                {g.label !== null && <div className="mf-nav-group">{g.label}</div>}
                {g.slides.map((sl) => (
                  <NavLink
                    key={sl.id}
                    to={withQs(`/${String(module)}/${sl.id}`)}
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
    <>
      {/* The brand head: the cherry and the wordmark, a link home. It is the rail's HALF of the
          header row — its own grid item in row 1, not a row inside the rail — so its underline
          lands on the same track as the top bar's, one continuous rule across the window, and
          the two can never drift apart. Collapsed, the wordmark folds away and the cherry grows
          to fill the strip (see `.shell-rail-collapsed` in styles.css). */}
      <a className="brand-block" href="/" aria-label="cherrypick — home">
        {/* Two berries touching at the centre, stems rising from each and joining at one knot,
            a single leaf off it. Stems in the dim text colour — green strokes were what made the
            old mark read as "a green thing", not a cherry. */}
        <svg className="brand-mark" width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="8.2" cy="15.5" r="4.6" fill="var(--accent-live)" />
          <circle cx="15.8" cy="15.5" r="4.6" fill="var(--accent-live)" opacity="0.88" />
          <path d="M8.2 15.2C7.6 10.4 8.4 6.4 12.1 4.2" stroke="var(--text-dim)" strokeWidth="1.4" strokeLinecap="round" />
          <path d="M15.8 15.2C16.4 10.4 15.6 6.4 12.1 4.2" stroke="var(--text-dim)" strokeWidth="1.4" strokeLinecap="round" />
          <path d="M12.1 4.2c2.2-.3 4.2-1.2 5.2-2.9-2.4-.2-4.4.6-5.6 2.2Z" fill="var(--pos)" />
        </svg>
        <span className="brand-name">cherrypick</span>
      </a>
      <nav className={expanded ? "mf-nav" : "mf-nav mf-nav-collapsed"} aria-label="modules">
      {/* The scrollable middle: the links (or the collapsed icon strip) scroll here. The brand
          head is the grid's own row above; the toggle below is the rail's last row, so
          collapsing never asks you to scroll a long module's tab list to reach the control. */}
      <div className="mf-nav-scroll">
      <div id="mf-nav-links" hidden={!expanded}>
        {above.map((l) => link(l))}
        <div className="mf-nav-section">Modules</div>
        {modules.map((l) => link(l))}
        {research.length > 0 && <div className="mf-nav-section">Research</div>}
        {research.map((l) => link(l))}
        <div className="mf-nav-section">Suite</div>
        {suitePages.map((l) => link(l))}
        {link(
          CONFIG_LINK,
          dirty > 0 && <span className="nav-dot" title={`${String(dirty)} unsaved change${dirty === 1 ? "" : "s"}`} />,
        )}
      </div>
      {/* The collapsed rail's icon strip: the same pages, same order, each icon a real Link to
          the page's root (the root is what an icon names). A name per link (aria-label) because
          the glyph carries no text; the expanded list stays as the tab order's text links. */}
      <div className="mf-nav-icons" hidden={expanded}>
        {[...above, ...modules, ...below, CONFIG_LINK].map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className="mf-nav-ic-wrap"
            aria-label={l.label}
            title={shortcutTitle(l, modules.includes(l))}
          >
            <NavIcon to={l.to} />
            {l.to === "/config" && dirty > 0 && <span className="nav-dot" />}
            {isExperimental(l.to.slice(1)) && <span className="mf-nav-ic-exp" />}
            {l.to.slice(1) === module && <span className="mf-nav-ic-on" />}
          </NavLink>
        ))}
      </div>
      </div>
      <button
        type="button"
        className="mf-nav-toggle"
        aria-expanded={expanded}
        aria-controls="mf-nav-links"
        title={expanded ? "collapse the navigation" : "expand the navigation"}
        onClick={toggleRail}
      >
        {expanded ? "«" : "»"}
      </button>
    </nav>
    </>
  );
}
