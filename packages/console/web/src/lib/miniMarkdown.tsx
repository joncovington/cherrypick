import { Fragment, type ReactNode } from "react";

/**
 * The little Markdown a study writes beside its result file (2026-10-30): `#` headings, pipe
 * tables, `-` lists and paragraphs, with `**bold**` and `` `code` `` inside them. Everything is
 * built as React elements, never as HTML, so nothing in the file can inject markup. Anything else
 * reads as a paragraph.
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "list"; items: string[] }
  | { kind: "para"; text: string };

const cells = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

const isRule = (line: string): boolean => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

export function parseBlocks(md: string): Block[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (line.trim() === "") {
      i += 1;
    } else if (h) {
      out.push({ kind: "heading", level: h[1]!.length, text: h[2]!.trim() });
      i += 1;
    } else if (line.trim().startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1]!)) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i]!.trim().startsWith("|")) rows.push(cells(lines[i++]!));
      out.push({ kind: "table", head, rows });
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]!)) items.push(lines[i++]!.replace(/^\s*[-*]\s+/, ""));
      out.push({ kind: "list", items });
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i]!.trim() !== "" && !/^#{1,6}\s/.test(lines[i]!) && !lines[i]!.trim().startsWith("|") && !/^\s*[-*]\s+/.test(lines[i]!)) {
        para.push(lines[i++]!.trim());
      }
      out.push({ kind: "para", text: para.join(" ") });
    }
  }
  return out;
}

/** `**bold**` and `` `code` `` within a line; the rest is text. */
export function inline(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter((p) => p !== "");
  return parts.map((p, k) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={k}>{p.slice(2, -2)}</strong>
    ) : p.startsWith("`") && p.endsWith("`") ? (
      <code key={k}>{p.slice(1, -1)}</code>
    ) : (
      <Fragment key={k}>{p}</Fragment>
    ),
  );
}

const FIGURE = /^[−+-]?\$?[−+-]?[\d,.]+%?$/;

/** A study's verdict words read as the board's chips; anything else is text. */
const VERDICT_CHIP: Record<string, string> = {
  pass: "chip chip-ok",
  fail: "chip chip-missing",
  "no verdict": "chip",
  reported: "chip muted",
};

/** A signed dollar figure takes the P&L colours; a count or a rate stays plain. */
function cellClass(c: string): string | undefined {
  if (!FIGURE.test(c)) return undefined;
  if (c.includes("$") && /^[−-]/.test(c)) return "num pnl-neg";
  if (c.includes("$") && c.startsWith("+")) return "num pnl-pos";
  return "num";
}

function Cell({ text }: { text: string }) {
  const chip = VERDICT_CHIP[text];
  return chip ? <span className={chip}>{text}</span> : <>{inline(text)}</>;
}

export function MiniMarkdown({ source }: { source: string }) {
  return (
    <div className="mini-md">
      {parseBlocks(source).map((b, k) => {
        if (b.kind === "heading") {
          const Tag = (`h${Math.min(b.level + 3, 6)}` as "h4" | "h5" | "h6");
          return <Tag key={k}>{inline(b.text)}</Tag>;
        }
        if (b.kind === "list") {
          return (
            <ul key={k}>
              {b.items.map((it, j) => (
                <li key={j}>{inline(it)}</li>
              ))}
            </ul>
          );
        }
        if (b.kind === "para") return <p key={k}>{inline(b.text)}</p>;
        return (
          <div key={k} className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  {b.head.map((c, j) => (
                    <th key={j} className={j === 0 ? undefined : "num"}>
                      {inline(c)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j}>
                    {r.map((c, m) => (
                      <td key={m} className={cellClass(c)}>
                        <Cell text={c} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
