import { Link, useInRouterContext } from "react-router-dom";

/**
 * A "Read the guide →" link from a help box or a Help tab to the guide section that explains it.
 *
 * `to` is a string literal at every call site (`<GuideLink to="/help/live#today" />`), never built
 * from parts: `tools/check_guide.py` (rule 2) scans the source for `/help/<slug>#<anchor>` literals and
 * fails when the guide has no such page or heading, and a computed href would hide the link from it.
 * The type only admits a guide path, so a stray URL does not compile.
 *
 * Inside the app it is a router link (no page reload). A help box rendered with no router around it,
 * as a unit test does, gets a plain anchor to the same address.
 */
export type GuidePath = `/help/${string}`;

export function GuideLink({ to, label = "Read the guide →" }: { to: GuidePath; label?: string }) {
  const routed = useInRouterContext();
  return <p className="help-guide">{routed ? <Link to={to}>{label}</Link> : <a href={to}>{label}</a>}</p>;
}
