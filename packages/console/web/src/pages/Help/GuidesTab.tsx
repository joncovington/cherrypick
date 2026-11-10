import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { headingSlug, markdownHeadings, type HelpIndexGroup, type HelpIndexPage } from "@console/shared";
import { useHelpIndex, useHelpPage } from "../../lib/api";
import { GuideMarkdown } from "./GuideMarkdown";

/** Every page in index order: what prev/next step through. */
export function flatPages(groups: readonly HelpIndexGroup[]): HelpIndexPage[] {
  return groups.flatMap((g) => g.pages);
}

/** The sidebar after a search: a page stays when its title, description or a heading matches. */
export function filterIndex(groups: readonly HelpIndexGroup[], query: string): HelpIndexGroup[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [...groups];
  const hit = (s: string) => s.toLowerCase().includes(q);
  return groups
    .map((g) => ({ ...g, pages: g.pages.filter((p) => hit(p.title) || hit(p.description) || p.headings.some(hit)) }))
    .filter((g) => g.pages.length > 0);
}

/** The headings of a page that match a search, so the sidebar can link straight to them. */
function matchingHeadings(page: HelpIndexPage, query: string): string[] {
  const q = query.trim().toLowerCase();
  return q === "" ? [] : page.headings.filter((h) => h.toLowerCase().includes(q)).slice(0, 4);
}

function pageHref(slug: string, anchor?: string): string {
  return `/help/${slug}${anchor ? `#${anchor}` : ""}`;
}

/**
 * Help → guides: the table of contents on the left (grouped as docs/guide/README.md groups it, with a
 * search over titles and headings), the page in the middle, "On this page" on the right, and the
 * previous and next pages in index order at the foot.
 */
export function GuidesTab() {
  const { slug: param } = useParams();
  const { hash } = useLocation();
  const index = useHelpIndex();
  const [query, setQuery] = useState("");
  const groups = index.data?.groups ?? [];
  const pages = flatPages(groups);
  const slugs = useMemo(() => new Set(pages.map((p) => p.slug)), [pages]);
  const slug = param === undefined || param === "guides" ? (pages[0]?.slug ?? null) : param;
  const page = useHelpPage(index.data === undefined ? null : slug);
  // On a narrow screen the index folds behind a toggle above the page; opening a page folds it again.
  const [tocOpen, setTocOpen] = useState(false);
  useEffect(() => setTocOpen(false), [slug, hash]);

  // A deep link's anchor: scroll to it once the page has drawn.
  useEffect(() => {
    if (page.data === undefined || hash === "") return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ block: "start" });
  }, [page.data, hash]);

  const at = pages.findIndex((p) => p.slug === slug);
  const prev = at > 0 ? pages[at - 1] : undefined;
  const next = at >= 0 && at < pages.length - 1 ? pages[at + 1] : undefined;
  const onThisPage = useMemo(() => {
    if (page.data === undefined) return [];
    const seen = new Map<string, number>();
    return markdownHeadings(page.data.markdown).map((h) => ({ ...h, id: headingSlug(h.text, seen) }));
  }, [page.data]);

  if (index.isError || index.data?.error) {
    return <div className="card">The guide index could not be read{index.data?.error ? `: ${index.data.error}` : ""}.</div>;
  }

  return (
    <div className="guide-layout">
      <nav className={tocOpen ? "guide-toc open" : "guide-toc"} aria-label="guides">
        <button type="button" className="guide-toc-toggle" aria-expanded={tocOpen} aria-controls="guide-toc-body" onClick={() => setTocOpen((o) => !o)}>
          <span className="guide-toc-toggle-label">Guides</span>
          <span className="guide-toc-toggle-current">{at >= 0 ? pages[at]!.title : ""}</span>
          <span aria-hidden="true">{tocOpen ? "▴" : "▾"}</span>
        </button>
        <div id="guide-toc-body" className="guide-toc-body">
          <input
            type="search"
            className="guide-search"
            placeholder="search the guides"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="search the guides"
          />
          {filterIndex(groups, query).map((g) => (
            <div key={g.title} className="guide-toc-group">
              <div className="guide-toc-title">{g.title}</div>
              {g.pages.map((p) => (
                <div key={p.slug}>
                  <Link to={pageHref(p.slug)} className="guide-toc-link" aria-current={p.slug === slug ? "page" : undefined} title={p.description}>
                    {p.title}
                  </Link>
                  {matchingHeadings(p, query).map((h) => (
                    <Link key={h} to={pageHref(p.slug, headingSlug(h))} className="guide-toc-link guide-toc-sub">
                      {h}
                    </Link>
                  ))}
                </div>
              ))}
            </div>
          ))}
          {query !== "" && filterIndex(groups, query).length === 0 && <div className="muted">nothing matches “{query}”</div>}
        </div>
      </nav>

      <article className="guide-page">
        {page.isError && <div className="card">There is no guide page “{slug}”. Pick one from the list.</div>}
        {page.data !== undefined && <GuideMarkdown markdown={page.data.markdown} slugs={slugs} />}
        {page.data !== undefined && (
          <div className="guide-prevnext">
            {prev ? (
              <Link to={pageHref(prev.slug)} className="guide-prev">
                ← {prev.title}
              </Link>
            ) : (
              <span />
            )}
            {next && (
              <Link to={pageHref(next.slug)} className="guide-next">
                {next.title} →
              </Link>
            )}
          </div>
        )}
      </article>

      {onThisPage.filter((h) => h.level === 2).length > 1 && (
        <nav className="guide-onpage" aria-label="on this page">
          <div className="guide-toc-title">On this page</div>
          {onThisPage
            .filter((h) => h.level === 2)
            .map((h) => (
              <a key={h.id} href={`#${h.id}`} className="guide-toc-link">
                {h.text}
              </a>
            ))}
        </nav>
      )}
    </div>
  );
}
