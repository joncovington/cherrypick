import { useRef, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { headingSlug, plainHeading } from "@console/shared";

const REPO_BLOB = "https://github.com/joncovington/cherrypick/blob/main/";

export type GuideLink = { kind: "help"; to: string } | { kind: "anchor"; to: string } | { kind: "external"; to: string };

/**
 * Where a link in a guide goes on the Help page. The guides are written to read on GitHub too, so
 * they link the way GitHub resolves: `other.md#x` is another guide (here, `/help/other#x`), and any
 * other relative path is a repo file (here, that file on GitHub's main branch, in a new tab).
 */
export function guideLink(href: string, slugs: ReadonlySet<string>): GuideLink {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { kind: "external", to: href };
  if (href.startsWith("#")) return { kind: "anchor", to: href };
  const m = /^(?:\.\/)?([a-z0-9-]+)\.md(#.*)?$/.exec(href);
  if (m && slugs.has(m[1]!)) return { kind: "help", to: `/help/${m[1]!}${m[2] ?? ""}` };
  const resolved = new URL(href, "https://repo.invalid/docs/guide/");
  return { kind: "external", to: `${REPO_BLOB}${resolved.pathname.replace(/^\//, "")}${resolved.hash}` };
}

/** A guide's image (`../images/x.png`) as the console serves it; anything else unchanged. */
export function guideImage(src: string): string {
  const m = /^(?:\.\.\/images\/|\.\.\/\.\.\/docs\/images\/)([A-Za-z0-9][A-Za-z0-9._-]*)$/.exec(src);
  return m ? `/api/help/images/${m[1]!}` : src;
}

/**
 * A guide's screenshot, shown at the column's width and opened at full size on a click. The full-size
 * view is a native modal dialog: Esc, a click anywhere on it, or its close button shuts it, and focus
 * goes back to the image.
 */
export function GuideImage({ src, alt }: { src: string; alt: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button
        type="button"
        className="guide-img-btn"
        aria-label={alt ? `Expand image: ${alt}` : "Expand image"}
        title="Click to expand"
        onClick={() => dialog.current?.showModal()}
      >
        <img src={src} alt={alt} className="guide-img" loading="lazy" />
      </button>
      <dialog ref={dialog} className="guide-img-dialog" aria-label={alt || "Image"} onClick={() => dialog.current?.close()}>
        <button type="button" className="guide-img-close" aria-label="Close">
          ×
        </button>
        <img src={src} alt={alt} />
      </dialog>
    </>
  );
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node !== null && typeof node === "object" && "props" in node) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

/**
 * One guide's markdown. GitHub-flavoured (tables, task lists), no raw HTML: react-markdown drops it
 * unless a raw-HTML plugin is added, and none is. Headings carry GitHub's anchor ids, so a link that
 * works on GitHub works here.
 */
export function GuideMarkdown({ markdown, slugs, headingOffset = 0 }: { markdown: string; slugs: ReadonlySet<string>; headingOffset?: number }) {
  const seen = new Map<string, number>();
  const heading =
    (level: number) =>
    ({ children }: { children?: ReactNode }) => {
      const id = headingSlug(plainHeading(textOf(children)), seen);
      const Tag = `h${String(Math.min(6, level + headingOffset))}` as "h2";
      return (
        <Tag id={id} className="guide-h">
          {children}
          <a className="guide-anchor" href={`#${id}`} aria-label="link to this section">
            #
          </a>
        </Tag>
      );
    };
  const components: Components = {
    h1: heading(1),
    h2: heading(2),
    h3: heading(3),
    h4: heading(4),
    a: ({ href, children }) => {
      const link = guideLink(href ?? "", slugs);
      if (link.kind === "help") return <Link to={link.to}>{children}</Link>;
      if (link.kind === "anchor") return <a href={link.to}>{children}</a>;
      return (
        <a href={link.to} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    img: ({ src, alt }) => <GuideImage src={guideImage(typeof src === "string" ? src : "")} alt={alt ?? ""} />,
    table: ({ children }) => (
      <div className="guide-table-wrap">
        <table>{children}</table>
      </div>
    ),
  };
  return (
    <div className="guide-md">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </Markdown>
    </div>
  );
}
